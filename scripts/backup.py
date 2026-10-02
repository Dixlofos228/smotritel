#!/usr/bin/env python3

import argparse
import fcntl
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import time


def backup(source, destination):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if destination.is_relative_to(source):
        raise ValueError("Backup destination must be outside the archive volume")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (source / "process.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Stop bot before backup; archive is locked") from None
        with tempfile.TemporaryDirectory(dir=destination) as stage:
            copy = Path(stage) / "archive.sqlite3"
            origin = sqlite3.connect(f"file:{source / 'archive.sqlite3'}?mode=ro", uri=True)
            target = sqlite3.connect(copy)
            origin.backup(target)
            assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            origin.close()
            target.close()
            output = destination / f"smotritel-{time.time_ns()}.tar"
            with tarfile.open(output, "w") as tar:
                tar.add(copy, arcname="archive.sqlite3")
                for folder in ("media", "generated", "tenants"):
                    if (source / folder).exists():
                        tar.add(source / folder, arcname=folder)
            output.chmod(0o600)
    return output


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    args = parser.parse_args()
    print(backup(os.getenv("DATA_DIR", "/data"), args.destination))
