from dataclasses import dataclass
from pathlib import Path
import os
from urllib.parse import urlparse


def secret(name):
    file = os.getenv(name + "_FILE")
    return Path(file).read_text().strip() if file and Path(file).exists() else os.getenv(name, "").strip()


def local_url(url):
    p = urlparse(url)
    return p.scheme in ("http", "https") and p.hostname in ("localhost", "127.0.0.1", "::1", "ollama", "stt")


@dataclass
class Config:
    data_dir: Path
    token: str = ""
    owner_id: int = 0
    pairing_code: str = ""
    ai_provider: str = "ollama"
    ai_url: str = "http://ollama:11434"
    ai_model: str = "qwen2.5:3b"
    ai_key: str = ""
    external_ai: bool = False
    stt_url: str = "http://stt:8090"
    weather: bool = False
    test_chat: int = 0
    max_media: int = 20 * 1024 * 1024
    budget: int = 10000 * 1024 * 1024

    tiktok: bool = False
    api_port: int = 8787
    system_admin_id: int = 0
    timezone: str = "America/Montevideo"
    tenant_media_budget: int = 1024 * 1024 * 1024

    @classmethod
    def load(cls):
        return cls(
            Path(os.getenv("DATA_DIR", "./data")),
            secret("BOT_TOKEN"),
            0,
            "",
            os.getenv("AI_PROVIDER", "ollama"),
            os.getenv("AI_BASE_URL", "http://ollama:11434"),
            os.getenv("AI_MODEL", "qwen2.5:3b"),
            secret("AI_API_KEY"),
            os.getenv("ALLOW_EXTERNAL_AI", "false").lower() == "true",
            os.getenv("STT_URL", "http://stt:8090"),
            os.getenv("ALLOW_WEATHER", "false").lower() == "true",
            int(os.getenv("TEST_CHAT_ID", "0")),
            int(os.getenv("MAX_MEDIA_MB", "20")) * 1024 * 1024,
            int(os.getenv("ARCHIVE_BUDGET_MB", "10000")) * 1024 * 1024,
            os.getenv("ALLOW_TIKTOK", "false").lower() == "true",
            int(os.getenv("LOCAL_API_PORT", "8787")),
            int(os.getenv("SYSTEM_ADMIN_ID", "0")),
            os.getenv("TIME_ZONE", "America/Montevideo"),
            int(os.getenv("TENANT_MEDIA_BUDGET_MB", "1024")) * 1024 * 1024,
        )
