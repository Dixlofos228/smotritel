import asyncio
import os
import tempfile
from pathlib import Path
from aiohttp import web
from faster_whisper import WhisperModel
import av

MODEL = None
LOCK = asyncio.Semaphore(1)
LIMIT = 20 * 1024 * 1024


def transcribe(path):
    with av.open(path) as container:
        duration = (container.duration or 0) / av.time_base
        if duration > 600:
            raise ValueError("duration_limit")
    parts, info = MODEL.transcribe(path, beam_size=1, vad_filter=True)
    res = []
    for part in parts:
        if part.end > 600:
            raise ValueError("duration_limit")
        res.append(part.text.strip())
    return {"text": " ".join(res), "language": info.language}


async def audio(request):
    if MODEL is None:
        return web.json_response({"error": "model_not_ready"}, status=503)
    tmp = None
    async with LOCK:
        try:
            reader = await request.multipart()
            field = await reader.next()
            if field is None or field.name != "audio":
                return web.json_response({"error": "audio_required"}, status=400)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".audio") as f:
                tmp = Path(f.name)
                size = 0
                while chunk := await field.read_chunk(65536):
                    size += len(chunk)
                    if size > LIMIT:
                        return web.json_response({"error": "file_too_large"}, status=413)
                    f.write(chunk)
            return web.json_response(await asyncio.to_thread(transcribe, tmp))
        except Exception:
            return web.json_response({"error": "invalid_audio_or_duration_limit"}, status=422)
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)


async def startup(app):
    global MODEL
    MODEL = await asyncio.to_thread(
        WhisperModel,
        os.getenv("STT_MODEL", "tiny"),
        device="cpu",
        compute_type="int8",
        download_root="/models",
        cpu_threads=4,
    )


app = web.Application(client_max_size=LIMIT)
app.router.add_post("/transcribe", audio)
app.router.add_get("/health", lambda request: web.json_response({"ready": MODEL is not None}))
app.on_startup.append(startup)
if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8090, access_log=None)
