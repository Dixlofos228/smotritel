#!/usr/bin/env python3

import argparse
import json
import os
from pathlib import Path
import sqlite3


def verify(directory):
    path = Path(directory) / "archive.sqlite3"
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    deletion = db.execute(
        "SELECT m.connection_id,m.chat_id,m.message_id,m.deleted_at FROM messages m JOIN versions v USING(connection_id,chat_id,message_id) WHERE v.text='TEST DELETE 12345' AND m.deleted_at IS NOT NULL"
    ).fetchall()
    edits = db.execute(
        "SELECT connection_id,chat_id,message_id FROM versions WHERE text IN ('TEST EDIT ORIGINAL','TEST EDIT CHANGED') GROUP BY connection_id,chat_id,message_id HAVING COUNT(DISTINCT text)=2"
    ).fetchall()
    res = {
        "deleted_original_found": [dict(r) for r in deletion],
        "both_edit_versions_found": [dict(r) for r in edits],
        "database_integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
    }
    db.close()
    return res


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR", "/data"))
    args = parser.parse_args()
    result = verify(args.data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(
        0
        if result["deleted_original_found"]
        and result["both_edit_versions_found"]
        and result["database_integrity"] == "ok"
        else 1
    )
