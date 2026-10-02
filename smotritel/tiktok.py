# загрузка тиктока
import asyncio
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse
import uuid
import json
import re
import io
from html.parser import HTMLParser
import aiohttp


class DownloadUnavailable(Exception):
    pass


def validate_url(url):
    p = urlparse(url)
    if (
        p.scheme != "https"
        or p.hostname not in ("www.tiktok.com", "tiktok.com", "vm.tiktok.com", "vt.tiktok.com")
        or p.username
        or p.password
        or p.port not in (None, 443)
        or any(c.isspace() for c in url)
    ):
        raise DownloadUnavailable("Нужна обычная публичная HTTPS-ссылка TikTok")
    return url


def image_urls(page, post_id):

    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.active = False
            self.parts = []
            self.documents = []

        def handle_starttag(self, tag, attrs):
            if tag == "script" and dict(attrs).get("id") in (
                "SIGI_STATE",
                "__UNIVERSAL_DATA_FOR_REHYDRATION__",
            ):
                self.active = True
                self.parts = []

        def handle_data(self, data):
            if self.active:
                self.parts.append(data)

        def handle_endtag(self, tag):
            if tag == "script" and self.active:
                self.documents.append("".join(self.parts))
                self.active = False

    parser = Scripts()
    parser.feed(page)
    todo = []
    for doc in parser.documents:
        try:
            todo.append(json.loads(doc))
        except ValueError:
            continue
    nodes = 0
    while todo and nodes < 10000:
        item = todo.pop()
        nodes += 1
        if isinstance(item, dict):
            if str(item.get("id", item.get("aweme_id", ""))) == str(post_id):
                photos = item.get("imagePost", item.get("image_post_info", {})).get("images", [])
                res = []
                for photo in photos:
                    urls = photo.get("imageURL", photo.get("image_url", {}))
                    opts = urls.get("urlList", urls.get("url_list", []))
                    if opts:
                        res.append(opts[0])
                if res:
                    return res[:20]
            todo.extend(item.values())
        elif isinstance(item, list):
            todo.extend(item)
    raise DownloadUnavailable(
        "Фото этой публичной публикации не переданы TikTok Возможно, нужен вход или публикация недоступна"
    )


def validate_cdn(url):
    p = urlparse(url)
    allowed = (
        "tiktokcdn.com",
        "tiktokcdn-us.com",
        "byteoversea.com",
        "ibytedtos.com",
        "tiktokcdn-eu.com",
        "tikwm.com",
        "tiktokv.com",
        "akamaized.net",
    )
    if (
        p.scheme != "https"
        or p.username
        or p.password
        or p.port not in (None, 443)
        or not any(p.hostname == h or (p.hostname or "").endswith("." + h) for h in allowed)
    ):
        raise DownloadUnavailable("TikTok вернул неподдерживаемый адрес фото")
    return url


async def bounded_get(session, url, limit, source=False):
    for _ in range(5):
        validate_url(url) if source else validate_cdn(url)
        async with session.get(url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                from urllib.parse import urljoin

                url = urljoin(url, resp.headers.get("Location", ""))
                continue
            if resp.status != 200:
                raise DownloadUnavailable("Публичная публикация TikTok недоступна")
            body = bytearray()
            async for chunk in resp.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > limit:
                    raise DownloadUnavailable("Превышен лимит размера TikTok")
            return url, bytes(body)
    raise DownloadUnavailable("Слишком много перенаправлений TikTok")


async def photos(url, root, limit, guard=None):
    from PIL import Image

    folder = Path(root) / ("tiktok-" + uuid.uuid4().hex)
    folder.mkdir(parents=True, mode=0o700)
    try:
        async with aiohttp.ClientSession() as session:
            final, page = await bounded_get(session, url, 2 * 1024 * 1024, source=True)
            match = re.search(r"/(?:photo|video)/(\d+)", urlparse(final).path)
            if not match:
                raise DownloadUnavailable("Нужна ссылка на одну публикацию TikTok")
            urls = image_urls(page.decode("utf-8", errors="replace"), match[1])
            paths = []
            total = 0
            for i, image_url in enumerate(urls):
                if guard:
                    guard()
                _, body = await bounded_get(session, image_url, limit - total)
                total += len(body)
                with Image.open(io.BytesIO(body)) as image:
                    if image.width * image.height > 25_000_000:
                        raise DownloadUnavailable("Фото слишком большое")
                    image = image.convert("RGB")
                    image.thumbnail((2160, 3840))
                    path = folder / f"slide-{i + 1:02}.jpg"
                    if guard:
                        guard()
                    image.save(path, quality=90)
                    path.chmod(0o600)
                    paths.append(path)
            if sum(p.stat().st_size for p in paths) > limit:
                raise DownloadUnavailable("Слайдшоу превышает лимит размера")
            if guard:
                guard()
            return paths
    except (aiohttp.ClientError, TimeoutError, ValueError, OSError, DownloadUnavailable):
        shutil.rmtree(folder, ignore_errors=True)
        raise DownloadUnavailable(
            "Фото TikTok недоступны или превышен лимит размера Попробуйте другую публичную ссылку"
        ) from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


async def download_direct(url, root, limit, guard=None):
    validate_url(url)
    if guard:
        guard()
    if "/photo/" in url:
        return await photos(url, root, limit, guard)
    try:
        path = await download(url, root, limit)
    except DownloadUnavailable:
        return await photos(url, root, limit, guard)
    if guard:
        try:
            guard()
        except ValueError:
            shutil.rmtree(path.parent, ignore_errors=True)
            raise
    return [path]


async def download_all(url, root, limit, guard=None):
    from PIL import Image
    from urllib.parse import urljoin

    validate_url(url)
    if guard:
        guard()
    folder = Path(root) / ("tiktok-" + uuid.uuid4().hex)
    folder.mkdir(parents=True, mode=0o700)
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": "Smotritel/1.0"}) as session:
            async with session.get(
                "https://www.tikwm.com/api/",
                params={"url": url, "hd": "1"},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise DownloadUnavailable("Загрузчик TikTok временно недоступен")
                body = bytearray()
                async for chunk in resp.content.iter_chunked(32768):
                    body.extend(chunk)
                    if len(body) > 256 * 1024:
                        raise DownloadUnavailable("Ответ загрузчика слишком большой")
                obj = json.loads(body)
            if not isinstance(obj, dict):
                raise DownloadUnavailable("Загрузчик вернул некорректный ответ")
            data = obj.get("data")
            if obj.get("code") != 0 or not isinstance(data, dict):
                raise DownloadUnavailable("Публикация TikTok недоступна Проверьте публичную ссылку")
            images = data.get("images") or []
            if images and (not isinstance(images, list) or len(images) > 20):
                raise DownloadUnavailable("В публикации больше 20 фото Превышен лимит одной загрузки")

            # wmplay с водяным знаком, его не берём
            play = data.get("hdplay") if 0 < int(data.get("hd_size") or 0) <= limit else data.get("play")
            urls = images or [play]
            if not all(isinstance(u, str) and u for u in urls):
                raise DownloadUnavailable("Версия без водяного знака не передана загрузчиком")
            paths, total = [], 0
            for i, remote in enumerate(urls):
                if guard:
                    guard()
                remote = urljoin("https://www.tikwm.com/", remote)
                _, content = await bounded_get(session, remote, limit - total)
                total += len(content)
                path = folder / (f"slide-{i + 1:02}.jpg" if images else "video.mp4")
                if images:
                    with Image.open(io.BytesIO(content)) as im:
                        if im.width * im.height > 25_000_000:
                            raise DownloadUnavailable("Фото слишком большое")
                        im = im.convert("RGB")
                        im.thumbnail((2160, 3840))
                        if guard:
                            guard()
                        im.save(path, quality=94)
                else:
                    if b"ftyp" not in content[:40]:
                        raise DownloadUnavailable("Загрузчик не вернул корректное MP4 видео")
                    if guard:
                        guard()
                    path.write_bytes(content)
                path.chmod(0o600)
                paths.append(path)
            if sum(p.stat().st_size for p in paths) > limit:
                raise DownloadUnavailable("Публикация превышает лимит размера")
            if guard:
                guard()
            return paths
    except DownloadUnavailable:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError, OSError, TypeError):
        shutil.rmtree(folder, ignore_errors=True)
        raise DownloadUnavailable(
            "Не удалось загрузить TikTok без водяного знака Попробуйте позже или другую публичную ссылку"
        ) from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


async def download(url, root, limit):
    validate_url(url)
    folder = Path(root) / ("tiktok-" + uuid.uuid4().hex)
    folder.mkdir(parents=True, mode=0o700)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "smotritel.tiktok",
        url,
        str(folder),
        str(limit),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.wait(), 120)
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix in (".mp4", ".webm", ".mov")]
        if process.returncode or len(files) != 1 or files[0].stat().st_size > limit:
            raise DownloadUnavailable(
                "Публичное видео недоступно, слишком велико либо требуется вход/CAPTCHA Обход не выполняется Slideshow не поддерживается"
            )
        return files[0]
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
            await process.wait()
        shutil.rmtree(folder, ignore_errors=True)
        if asyncio.current_task().cancelling():
            raise
        raise DownloadUnavailable("Время загрузки TikTok истекло") from None
    except DownloadUnavailable:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def configure_public_only():
    from yt_dlp.extractor.tiktok import TikTokBaseIE
    from yt_dlp.utils import ExtractorError

    def refuse_challenge(self, webpage):
        raise ExtractorError("TikTok requires an anti-bot challenge; download stopped", expected=True)

    TikTokBaseIE._solve_challenge_and_set_cookies = refuse_challenge


def worker(url, folder, limit):
    import yt_dlp

    validate_url(url)
    configure_public_only()

    def bounded(progress):
        if progress.get("downloaded_bytes", 0) > limit:
            raise ValueError("size_limit")

    class Quiet:
        def debug(self, *args):
            pass

        warning = debug
        error = debug

    opts = {
        "quiet": True,
        "logger": Quiet(),
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 10,
        "retries": 1,
        "allowed_extractors": ["tiktok", "tiktokvm"],
        "max_filesize": limit,
        "format": f"best[ext=mp4][filesize<={limit}]/best[ext=mp4]/best",
        "outtmpl": str(Path(folder) / "video.%(ext)s"),
        "progress_hooks": [bounded],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


if __name__ == "__main__":
    worker(sys.argv[1], sys.argv[2], int(sys.argv[3]))
