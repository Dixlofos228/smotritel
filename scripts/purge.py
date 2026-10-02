#!/usr/bin/env python3

import argparse
import fcntl
import os
from pathlib import Path
import shutil


def purge(directory, confirmation):
    if confirmation != "DELETE-ALL":
        raise ValueError("Requires --confirm DELETE-ALL")
    root = Path(directory).resolve()
    if root in (Path("/"), Path.home(), Path("/home"), Path("/var")):
        raise ValueError("Unsafe data directory")
    with (root / "process.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Stop bot before purge; archive is locked") from None
        for name in ("media", "generated", "tenants"):
            path = root / name
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        for name in (
            "archive.sqlite3",
            "archive.sqlite3-wal",
            "archive.sqlite3-shm",
            "heartbeat.json",
            "heartbeat.tmp",
        ):
            (root / name).unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    purge(os.getenv("DATA_DIR", "/data"), args.confirm)
    print(
        "Telegram archive, owner binding, queues, watches and API key hash removed. Backups must be removed separately"
    )
