import json
import sys
import time
from .config import Config

try:
    h = json.loads((Config.load().data_dir / "heartbeat.json").read_text())
    healthy = (
        h["status"] == "running"
        and time.time() - h["time"] < 20
        and time.time() - h["last_poll"] < 90
        and time.time() - h["last_worker"] < 300
    )
except (OSError, KeyError, ValueError):
    healthy = False
sys.exit(0 if healthy else 1)
