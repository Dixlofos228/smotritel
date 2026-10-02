import json
from pathlib import Path
from urllib.parse import quote
import aiohttp


class TelegramError(Exception):
    def __init__(self, code, retry_after=0, description=""):
        self.not_modified = "message is not modified" in description.lower()
        self.edit_missing = (
            "message to edit not found" in description.lower()
            or "message can't be edited" in description.lower()
        )
        self.code, self.retry_after = code, retry_after
        super().__init__(f"Telegram API status {code}")


class Telegram:
    def __init__(self, session, token):
        self.session, self.token = session, token

    async def call(self, method, **payload):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            async with self.session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=65)) as r:
                obj = await r.json()
                if not obj.get("ok"):
                    raise TelegramError(
                        obj.get("error_code", r.status),
                        obj.get("parameters", {}).get("retry_after", 0),
                        obj.get("description", ""),
                    )
                return obj["result"]
        except TelegramError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise TelegramError(503) from None

    async def upload(self, method, payload, path, field="document", filename=None):
        form = aiohttp.FormData()
        for key, value in payload.items():
            form.add_field(key, json.dumps(value) if isinstance(value, (dict, list)) else str(value))
        try:
            with Path(path).open("rb") as file:
                form.add_field(
                    field, file, filename=filename or Path(path).name, content_type="application/octet-stream"
                )
                async with self.session.post(
                    f"https://api.telegram.org/bot{self.token}/{method}",
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=90),
                ) as r:
                    obj = await r.json()
                    if not obj.get("ok"):
                        raise TelegramError(
                            obj.get("error_code", r.status),
                            obj.get("parameters", {}).get("retry_after", 0),
                            obj.get("description", ""),
                        )
                    return obj["result"]
        except TelegramError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise TelegramError(503) from None

    async def download(self, file_id, target, limit):
        meta = await self.call("getFile", file_id=file_id)
        if meta.get("file_size", 0) > limit:
            raise TelegramError(413)
        path = quote(meta["file_path"], safe="/")
        try:
            async with self.session.get(
                f"https://api.telegram.org/file/bot{self.token}/{path}",
                timeout=aiohttp.ClientTimeout(total=120),
            ) as r:
                if r.status != 200:
                    raise TelegramError(r.status)
                size = 0
                with Path(target).open("wb") as out:
                    async for chunk in r.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > limit:
                            raise TelegramError(413)
                        out.write(chunk)
            return meta
        except TelegramError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise TelegramError(503) from None
