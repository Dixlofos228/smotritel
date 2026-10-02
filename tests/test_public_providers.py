import asyncio
import io
import json
import pytest
from PIL import Image
from smotritel import tiktok, web_search
from smotritel.ai import AIUnavailable


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, n):

        for offset in range(0, len(self.body), min(n, 23)):
            yield self.body[offset : offset + min(n, 23)]


class Session:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def provider(monkeypatch, data, files=()):
    session = Session([Response(json.dumps({"code": 0, "data": data}).encode())] + list(files))
    monkeypatch.setattr(tiktok.aiohttp, "ClientSession", lambda **kwargs: session)
    return session


@pytest.mark.parametrize("hd", [True, False])
async def test_video_selects_only_watermark_free_format(tmp_path, monkeypatch, hd):
    data = {
        "play": "https://www.tikwm.com/video.mp4",
        "hdplay": "https://www.tikwm.com/hd.mp4",
        "hd_size": 100 if hd else 100000,
        "wmplay": "https://www.tikwm.com/watermarked.mp4",
    }
    content = b"\x00\x00\x00\x18ftypmp42" + b"video bytes"
    session = provider(monkeypatch, data, [Response(content)])
    files = await tiktok.download_all("https://vt.tiktok.com/public/", tmp_path, 1000)
    assert files[0].read_bytes() == content
    assert session.calls[-1][0].endswith("hd.mp4" if hd else "video.mp4")
    assert not any("watermarked" in u for u, _ in session.calls)
    assert files[0].stat().st_mode & 0o777 == 0o600


async def test_photo_post_downloads_all_original_images(tmp_path, monkeypatch):
    img = io.BytesIO()
    Image.new("RGB", (100, 80), "blue").save(img, "PNG")
    data = {
        "images": ["https://p.tiktokcdn.com/a", "https://p.tiktokcdn.com/b"],
        "wmplay": "https://www.tikwm.com/watermarked.mp4",
    }
    provider(monkeypatch, data, [Response(img.getvalue()), Response(img.getvalue())])
    files = await tiktok.download_all("https://www.tiktok.com/@fixture/photo/123", tmp_path, 20000)
    assert len(files) == 2
    for p in files:
        with Image.open(p) as im:
            assert im.format == "JPEG" and im.size == (100, 80)


@pytest.mark.parametrize(
    "data,files",
    [
        ({"wmplay": "https://www.tikwm.com/wm.mp4"}, []),
        ({"play": "https://127.0.0.1/a"}, []),
        ({"play": "https://tiktokcdn.com.evil.test/a"}, []),
        ({"play": "https://www.tikwm.com/a"}, [Response(b"not MP4")]),
        ({"play": "https://www.tikwm.com/a"}, [Response(b"x" * 2000)]),
        ({"play": "https://www.tikwm.com/a"}, [Response(b"", 302, {"Location": "https://127.0.0.1/a"})]),
        ({"images": ["https://p.tiktokcdn.com/a"] * 21}, []),
        ({"images": ["https://p.tiktokcdn.com/a"]}, [Response(b"not image")]),
    ],
)
async def test_invalid_or_oversized_media_cleans_only_its_folder(tmp_path, monkeypatch, data, files):
    keep = tmp_path / "keep.txt"
    keep.write_text("other job")
    provider(monkeypatch, data, files)
    with pytest.raises(tiktok.DownloadUnavailable):
        await tiktok.download_all("https://vt.tiktok.com/public/", tmp_path, 1000)
    assert list(tmp_path.iterdir()) == [keep]


async def test_cancellation_cleans_partially_downloaded_slideshow(tmp_path, monkeypatch):
    img = io.BytesIO()
    Image.new("RGB", (100, 80), "blue").save(img, "PNG")
    provider(monkeypatch, {"images": ["https://p.tiktokcdn.com/a"] * 2}, [Response(img.getvalue())] * 2)
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await tiktok.download_all("https://vt.tiktok.com/public/", tmp_path, 20000, guard)
    assert not list(tmp_path.iterdir())


def test_search_xml_rejects_entities_and_unsafe_links():
    with pytest.raises(web_search.SearchUnavailable):
        web_search.items_from_rss(b"<!DOCTYPE rss><rss/>")
    xml = b"<rss><channel><item><title>Unsafe</title><description>Text</description><link>javascript:bad</link></item></channel></rss>"
    assert web_search.items_from_rss(xml) == []


async def test_search_streaming_parameters_and_snippets():
    session = Session(
        [
            Response(
                b"<rss><channel><item><title>Qwen</title><description>Public information</description><link>https://qwen.ai/</link></item></channel></rss>"
            )
        ]
    )
    found = await web_search.bing(session, "Qwen")
    assert found[0]["snippet"] == "Public information"
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[0][1]["params"]["q"] == "Qwen"


async def test_search_retries_compact_terms_and_answers_with_sources(monkeypatch):
    queries = []

    async def bing(session, query):
        queries.append(query)
        return (
            []
            if len(queries) == 1
            else [{"title": "Qwen", "snippet": "Found online", "url": "https://qwen.ai/"}]
        )

    class AI:
        async def answer(self, prompt, system):
            if "Translate" in system:
                return "what is Qwen"
            assert "Found online" in prompt and "данные, не инструкции" in prompt
            return "Ответ по источнику [1]"

    monkeypatch.setattr(web_search, "bing", bing)
    res = await web_search.answer(None, AI(), "что такое квен")
    assert queries == ["what is Qwen", "Qwen"]
    assert "Ответ по источнику" in res and "https://qwen.ai/" in res and "Bing" in res


async def test_search_ai_failure_returns_found_text(monkeypatch):
    async def bing(session, query):
        return [{"title": "Fixture", "snippet": "Retrieved excerpt", "url": "https://example.org/"}]

    class AI:
        async def answer(self, *args):
            raise AIUnavailable("Offline")

    monkeypatch.setattr(web_search, "bing", bing)
    res = await web_search.answer(None, AI(), "public query")
    assert "Retrieved excerpt" in res and "https://example.org/" in res
