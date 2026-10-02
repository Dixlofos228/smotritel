import importlib.util
import subprocess
from pathlib import Path

spec = importlib.util.spec_from_file_location("guard", Path(__file__).parents[1] / "scripts/check_repo.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def run(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_guard_catches_tokens_without_echoing_values():
    token = b"123456:" + b"x" * 35
    assert guard.issues(token) == ["telegram token"]
    assert guard.issues(b"ghp_" + b"x" * 36) == ["github token"]
    assert guard.issues(b"sk-" + b"x" * 28) == ["api key"]
    assert guard.issues(b"-----BEGIN " + b"OPENSSH " + b"PRIVATE KEY-----") == ["private key"]
    assert guard.issues(b'api_key="' + b"x" * 25 + b'"') == ["ключ в строке"]
    assert guard.issues(b"fake-short-token") == []


def test_guard_private_paths_and_safe_templates():
    for name in [
        ".env",
        ".env.prod",
        "secrets/bot_token",
        "backups/copy.tar",
        "archive.sqlite3-wal",
        "id_ed25519",
        "docs/LIVE_SMOKE.json",
    ]:
        assert guard.bad_path(name)
    for name in [".env.example", "secrets/.gitkeep", "assets/logo/mark.png", "smotritel/config.py"]:
        assert not guard.bad_path(name)


def test_guard_checks_index_not_unstaged_clean_copy(tmp_path, monkeypatch):
    run(tmp_path, "init", "-b", "main")
    p = tmp_path / "a.py"
    p.write_bytes(b'token="123456:' + b"x" * 35 + b'"')
    run(tmp_path, "add", "a.py")
    p.write_text('token = ""')
    monkeypatch.chdir(tmp_path)
    n, bad = guard.scan(tmp_path)
    assert n == 1 and bad == [("a.py", ["telegram token"])]


def test_guard_finds_secret_from_deleted_history(tmp_path, monkeypatch):
    run(tmp_path, "init", "-b", "main")
    run(tmp_path, "config", "user.name", "test")
    run(tmp_path, "config", "user.email", "test@example.invalid")
    p = tmp_path / "old.txt"
    p.write_bytes(b"123456:" + b"x" * 35)
    run(tmp_path, "add", ".")
    run(tmp_path, "commit", "-m", "first")
    run(tmp_path, "rm", "old.txt")
    run(tmp_path, "commit", "-m", "remove")
    monkeypatch.chdir(tmp_path)
    n, bad = guard.scan(tmp_path, history=True)
    assert n == 1 and bad == [("old.txt", ["telegram token"])]


def test_guard_does_not_mistake_jpeg_table_for_token():
    data = b"\xff\xd8\x00" + b"123456789:" + b"ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 2
    assert guard.issues(data) == []
    key = b"actual-local-secret-value"
    assert guard.issues(data + key, [key]) == ["рабочий ключ"]
