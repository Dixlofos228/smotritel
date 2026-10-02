import aiohttp
from .config import local_url


class AIUnavailable(Exception):
    pass


class AI:
    def __init__(self, config, session):
        self.c, self.session = config, session

    async def answer(self, prompt, system="Отвечай кратко на языке пользователя Не выдумывай факты"):
        if not local_url(self.c.ai_url) and not self.c.external_ai:
            raise AIUnavailable("Внешний AI выключен Разрешите ALLOW_EXTERNAL_AI только осознанно")
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt[:6000]}]
        headers = {"Authorization": "Bearer " + self.c.ai_key} if self.c.ai_key else {}
        # секретный deep seek IP
        if self.c.ai_provider == "ollama":
            url = self.c.ai_url.rstrip("/") + "/api/chat"
            data = {
                "model": self.c.ai_model,
                "messages": msgs,
                "stream": False,
                "options": {"num_predict": 192, "num_ctx": 4096, "temperature": 0.3},
            }
        elif self.c.ai_provider == "openai-compatible":
            url = self.c.ai_url.rstrip("/") + "/chat/completions"
            data = {"model": self.c.ai_model, "messages": msgs, "max_tokens": 192, "temperature": 0.3}
        else:
            raise AIUnavailable("Неизвестный AI_PROVIDER")
        try:
            async with self.session.post(
                url,
                json=data,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                if r.status != 200:
                    raise AIUnavailable(
                        f"AI не готов (HTTP {r.status}); проверьте загрузку модели и logs ollama."
                    )
                res = await r.json()
            text = (
                res["message"]["content"]
                if self.c.ai_provider == "ollama"
                else res["choices"][0]["message"]["content"]
            )
            if not text.strip():
                raise AIUnavailable("AI вернул пустой ответ")
            return text[:12000]
        except AIUnavailable:
            raise
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError, IndexError):
            raise AIUnavailable("Локальный AI недоступен или превышено время ожидания") from None

    # голосовые только локально
    async def transcript(self, path):

        if not local_url(self.c.stt_url):
            raise AIUnavailable("Расшифровка разрешена только локальному STT")
        form = aiohttp.FormData()
        try:
            with path.open("rb") as f:
                form.add_field("audio", f, filename=path.name, content_type="application/octet-stream")
                async with self.session.post(
                    self.c.stt_url.rstrip("/") + "/transcribe",
                    data=form,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=240),
                ) as r:
                    if r.status != 200:
                        raise AIUnavailable(f"Локальный STT не готов (HTTP {r.status}).")
                    return (await r.json())["text"]
        except AIUnavailable:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
            raise AIUnavailable("Локальный STT недоступен или превышено время ожидания") from None
