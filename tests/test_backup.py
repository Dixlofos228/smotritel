import importlib.util
from pathlib import Path
import pytest
from conftest import business, CID, PEER
from smotritel.db import DB


def script(name):
    p = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_backup_restore_purge_roundtrip(system, tmp_path):
    db, s, _, c = system
    await s.handle(business(10, "backup original", 7))
    archive = script("backup").backup(c.data_dir, tmp_path.with_name(tmp_path.name + "-backup"))
    target = tmp_path / "restored"
    script("restore").restore(archive, target)
    other = DB(target)
    assert other.history(CID, PEER, 7)[0]["text"] == "backup original"
    other.close()
    with pytest.raises(ValueError):
        script("restore").restore(archive, target)
    with pytest.raises(ValueError):
        script("purge").purge(target, "wrong")
    script("purge").purge(target, "DELETE-ALL")
    assert not (target / "archive.sqlite3").exists()
    assert archive.is_file()


def test_restore_rejects_path_traversal(tmp_path):
    import tarfile
    import io

    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as tar:
        i = tarfile.TarInfo("media/../../escaped")
        i.size = 4
        tar.addfile(i, io.BytesIO(b"evil"))
    with pytest.raises(ValueError, match="Unsafe"):
        script("restore").restore(archive, tmp_path / "new")
    assert not (tmp_path / "escaped").exists()


def test_backup_and_purge_refuse_while_bot_lock_is_held(system, tmp_path):
    import fcntl

    _, _, _, c = system
    with (c.data_dir / "process.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit, match="locked"):
            script("backup").backup(c.data_dir, tmp_path.with_name(tmp_path.name + "-backup"))
        with pytest.raises(SystemExit, match="locked"):
            script("purge").purge(c.data_dir, "DELETE-ALL")
    assert (c.data_dir / "archive.sqlite3").is_file()
