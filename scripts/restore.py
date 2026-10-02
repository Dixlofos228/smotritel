#!/usr/bin/env python3

import argparse
import fcntl
import os
from pathlib import Path
import sqlite3
import tarfile


def restore(archive, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (destination / "process.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Stop bot before restore") from None
        if any(p.name != "process.lock" for p in destination.iterdir()):
            raise ValueError("Destination is not empty. Restore to a new volume or explicitly purge first")
        with tarfile.open(archive, "r") as tar:
            members = tar.getmembers()
            for item in members:
                parts = Path(item.name).parts
                if not parts or parts[0] not in ("archive.sqlite3", "media", "generated", "tenants"):
                    raise ValueError("Unexpected backup member")
                if not (item.isfile() or item.isdir()) or not (
                    destination / item.name
                ).resolve().is_relative_to(destination):
                    raise ValueError("Unsafe backup member")
            tar.extractall(destination, members=members, filter="data")
        db = sqlite3.connect(destination / "archive.sqlite3")
        check = db.execute("PRAGMA integrity_check").fetchone()[0]
        db.close()
        if check != "ok":
            raise ValueError("Restored database failed integrity_check")


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    args = parser.parse_args()
    restore(args.archive, os.getenv("DATA_DIR", "/data"))
    print("Restore verified. Start bot")
