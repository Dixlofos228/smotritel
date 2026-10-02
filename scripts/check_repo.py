# проверка перед коммитом
import argparse
import re
import subprocess
import sys
from pathlib import Path

RX = (
    ("telegram token", rb"(?<![A-Za-z0-9_])\d{5,16}:[A-Za-z0-9_-]{25,80}(?![A-Za-z0-9_-])"),
    ("github token", rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ("api key", rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    ("aws key", rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ("private key", rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    (
        "ключ в строке",
        rb"(?i)\b(?:api[_-]?key|bot[_-]?token|client[_-]?secret|password|passwd|secret[_-]?key)\b\s*[:=]\s*[\"']([^\"'\r\n]{16,})[\"']",
    ),
    ("ключ в url", rb"https?://[^\s/:@]+:[^\s/@]{8,}@"),
)
SAFE = {".env.example", "secrets/.gitkeep"}
BAD_DIRS = {"data", "backups", "secrets", "tenants", "generated", "media", ".venv", "venv", "logs"}
BAD_ENDS = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".tar",
    ".zip",
    ".dump",
    ".backup",
    ".bak",
    ".log",
)


def git(*args, data=None):
    return subprocess.check_output(["git", *args], input=data, stderr=subprocess.DEVNULL)


def bad_path(name):
    name = name.replace("\\", "/")
    if name in SAFE:
        return False
    p = Path(name)
    return (
        any(x in BAD_DIRS for x in p.parts)
        or p.name == ".env"
        or p.name.startswith((".env", "id_rsa", "id_ed25519", "credentials"))
        or p.name.endswith(BAD_ENDS)
        or ".sqlite-" in p.name
        or ".sqlite3-" in p.name
        or ".db-" in p.name
        or ".tar" in p.name
        or name.startswith("docs/")
        and p.suffix == ".json"
    )


def known(root):
    vals = []
    for p in (root / "secrets").glob("*"):
        if p.is_file() and p.name != ".gitkeep":
            val = p.read_bytes().strip()
            if len(val) >= 16:
                vals.append(val)
    p = root / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            if re.search(r"TOKEN|PASSWORD|SECRET|PAIRING|API_KEY", key) and not key.endswith("_FILE"):
                val = val.strip().strip("\"'").encode()
                if len(val) >= 16:
                    vals.append(val)
    return vals


def issues(data, vals=()):
    # в jpeg таблицах тоже бывают цифры с двоеточием
    try:
        data.decode("utf-8")
        found = [label for label, rx in RX if re.search(rx, data)]
    except UnicodeDecodeError:
        found = []
    if any(val in data for val in vals):
        found.append("рабочий ключ")
    return found


def scan(root, history=False, all_files=False):
    vals, bad, n = known(root), [], 0
    if history:
        # проверяем в том числе удалённые файлы из старых коммитов
        rows = git("rev-list", "--objects", "--all").splitlines()
        for row in rows:
            oid, _, path = row.partition(b" ")
            if git("cat-file", "-t", oid.decode()).strip() != b"blob":
                continue
            n += 1
            tags = issues(git("cat-file", "-p", oid.decode()), vals)
            if tags:
                bad.append((path.decode(errors="replace") or oid.decode(), tags))
    else:
        names = (
            git("ls-files", "-z")
            if all_files
            else git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z")
        )
        for raw in filter(None, names.split(b"\0")):
            name = raw.decode()
            data = git("show", ":" + name)
            n += 1
            tags = issues(data, vals)
            if bad_path(name):
                tags.append("приватный файл")
            mode = git("ls-files", "-s", "--", name).split()[0]
            if mode == b"120000":
                tags.append("симлинк")
            if tags:
                bad.append((name, tags))
    return n, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--all", action="store_true", dest="all_files")
    args = ap.parse_args()
    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    n, bad = scan(root, args.history, args.all_files)
    if bad:
        print("коммит остановлен:")
        for name, tags in bad:
            # значения ключей не печатаем
            print(f"  {name}: {', '.join(tags)}")
        return 1
    print(f"ключей нет, проверено файлов: {n}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError):
        print("проверка git не завершилась, коммит остановлен")
        sys.exit(1)
