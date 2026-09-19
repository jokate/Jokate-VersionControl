"""리세이브(noise) 판정: is_resave_only, snap 의 tree.noise, Diff/format_diff/log 표시, DB 마이그레이션."""
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import __main__ as cli  # noqa: E402
from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402
from jokate import uasset  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "IA_Aim.uasset"
SAVED_HASH_OFF = 24   # legacy -9: tag(4) legacy(4) ue4(4) ue5(4) licensee(4) 다음 SavedHash 20B


def _need_fixture() -> bytes:
    if not FIXTURE.exists():
        pytest.skip(f"픽스처 없음: {FIXTURE} (MNYS/Content 의 IA_Aim.uasset 을 복사할 것)")
    return FIXTURE.read_bytes()


def _flip_saved_hash(data: bytes) -> bytes:
    b = bytearray(data)
    for i in range(SAVED_HASH_OFF, SAVED_HASH_OFF + 20):
        b[i] ^= 0xFF
    return bytes(b)


def test_is_resave_only_header_vs_export(tmp_path: Path) -> None:
    data = _need_fixture()
    orig = tmp_path / "orig.uasset"
    orig.write_bytes(data)
    # 요약 헤더 SavedHash 만 바뀜 → 리세이브만
    hashed = tmp_path / "hash.uasset"
    hashed.write_bytes(_flip_saved_hash(data))
    assert hashed.read_bytes() != data
    assert uasset.is_resave_only(orig, hashed) is True
    # 첫 export 직렬화 바이트 하나 변경 → 실제 변경
    pkg = uasset.read_package(orig)
    ex = next(e for e in pkg.exports if e.serial_size > 0)
    b = bytearray(data)
    b[ex.serial_offset] ^= 0xFF
    real = tmp_path / "real.uasset"
    real.write_bytes(bytes(b))
    assert uasset.is_resave_only(orig, real) is False
    # 파싱 불가 → False
    junk = tmp_path / "junk.uasset"
    junk.write_bytes(b"not a package")
    assert uasset.is_resave_only(orig, junk) is False


def test_snap_marks_noise_and_cli(tmp_path: Path, capsys) -> None:
    data = _need_fixture()
    root = tmp_path / "Proj"
    (root / "Content" / "Foo").mkdir(parents=True)
    cfgmod.init(root)
    asset = root / "Content" / "Foo" / "IA_Aim.uasset"
    asset.write_bytes(data)
    st = storemod.Store(cfgmod.load(root))
    s1, d1, _ = st.snap("first")
    assert s1 is not None and not d1.all_noise
    assert st.tree(s1.id)["Foo/IA_Aim.uasset"].noise is False

    asset.write_bytes(_flip_saved_hash(data))
    s2, d2, _ = st.snap("")
    assert s2 is not None and s2.id == 2
    assert not d2.empty and d2.all_noise
    assert len(d2.resave) == 1 and d2.real_modified == []
    assert d2.by_class()[d2.resave[0][1].cls or "?"]["resave"] == 1
    row = st.db.execute("SELECT noise FROM tree WHERE snapshot_id=? AND rel=?", (2, "Foo/IA_Aim.uasset")).fetchone()
    assert row[0] == 1
    assert st.tree(2)["Foo/IA_Aim.uasset"].noise is True
    text = storemod.format_diff(d2)
    assert "M~ Foo/IA_Aim.uasset" in text and "리세이브만" in text and "resave 1" in text
    # show() 로 다시 계산해도 동일
    _, d_show = st.show(2)
    assert d_show.all_noise

    # 실제 변경은 noise 아님
    pkg = uasset.read_package(FIXTURE)
    ex = next(e for e in pkg.exports if e.serial_size > 0)
    b = bytearray(_flip_saved_hash(data))
    b[ex.serial_offset] ^= 0xFF
    asset.write_bytes(bytes(b))
    s3, d3, _ = st.snap("")
    assert s3 is not None and not d3.all_noise and len(d3.real_modified) == 1
    assert "(리세이브만)" not in storemod.format_diff(d3)
    st.close()

    # 확정 모델: log 는 기본으로 확정 버전만. 자동 저장(#2, #3)은 작업 중 기록이라 --all 에서 들여써 보인다
    assert cli.main(["log", str(root), "--all"]) == 0
    out = [l.strip() for l in capsys.readouterr().out.splitlines()]
    line2 = next(l for l in out if l.startswith("#2"))
    line3 = next(l for l in out if l.startswith("#3"))
    line1 = next(l for l in out if l.startswith("#1"))
    assert line2.endswith("(리세이브만)")
    assert "(리세이브만)" not in line1 and "(리세이브만)" not in line3


def test_watch_format_line_resave() -> None:
    from jokate import watch
    old = storemod.TreeEntry("Foo/A.uasset", "a" * 64, 10, "InputAction")
    new = storemod.TreeEntry("Foo/A.uasset", "b" * 64, 10, "InputAction", noise=True)
    d = storemod.diff_trees({old.rel: old}, {new.rel: new})
    snap = storemod.Snapshot(5, 4, "auto", "", 0.0)
    line = watch.format_line(watch.PollResult(snap, d), now=0.0)
    assert "리세이브만 1" in line and "#5 auto" in line
    # 실제 변경만 있으면 표시 없음
    new2 = storemod.TreeEntry("Foo/A.uasset", "c" * 64, 10, "InputAction")
    d2 = storemod.diff_trees({old.rel: old}, {new2.rel: new2})
    assert "리세이브만" not in watch.format_line(watch.PollResult(snap, d2), now=0.0)


def test_migration_adds_noise_column(tmp_path: Path) -> None:
    root = tmp_path / "Proj"
    (root / "Content").mkdir(parents=True)
    cfgmod.init(root)
    cfg = cfgmod.load(root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    db_path = cfg.state_dir / "index.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript("""
    CREATE TABLE snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, parent INTEGER,
        kind TEXT NOT NULL CHECK(kind IN ('auto','label')), message TEXT NOT NULL DEFAULT '', ts REAL NOT NULL);
    CREATE TABLE tree(snapshot_id INTEGER NOT NULL REFERENCES snapshots(id), rel TEXT NOT NULL, sha TEXT NOT NULL,
        size INTEGER NOT NULL, cls TEXT NOT NULL DEFAULT '', deps TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(snapshot_id, rel));
    INSERT INTO snapshots(parent,kind,message,ts) VALUES(NULL,'label','old',1.0);
    INSERT INTO tree(snapshot_id,rel,sha,size,cls,deps) VALUES(1,'Foo/A.uasset','ab',3,'X','[]');
    """)
    db.commit()
    db.close()
    assert "noise" not in {r[1] for r in sqlite3.connect(db_path).execute("PRAGMA table_info(tree)")}

    st = storemod.Store(cfg)
    cols = {r[1] for r in st.db.execute("PRAGMA table_info(tree)").fetchall()}
    assert "noise" in cols
    assert st.db.execute("SELECT noise FROM tree WHERE snapshot_id=1").fetchone()[0] == 0
    e = st.tree(1)["Foo/A.uasset"]
    assert e.noise is False and e.cls == "X"
    # 다시 열어도 문제 없음(멱등)
    st.close()
    st2 = storemod.Store(cfg)
    assert st2.head().id == 1
    st2.close()
