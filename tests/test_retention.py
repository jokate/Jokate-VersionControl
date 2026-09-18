"""13단계: squash(묶기) · prune(보관기간) · gc(객체 정리) 와 웹 API."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402
from jokate import web  # noqa: E402

DAY = 86400


@pytest.fixture
def st(tmp_path: Path) -> storemod.Store:
    root = tmp_path / "Proj"
    (root / "Content" / "Foo").mkdir(parents=True)
    cfgmod.init(root)
    return storemod.Store(cfgmod.load(root))


def write(st: storemod.Store, name: str, data: bytes) -> None:
    (st.cfg.content / "Foo" / name).write_bytes(data)


def make_chain(st: storemod.Store, n: int, kind: str = "auto") -> list[int]:
    ids = []
    for i in range(n):
        write(st, "A.uasset", f"A-v{i}".encode())
        s, _, _ = st.snap("" if kind == "auto" else f"라벨{i}", kind=kind, force=True)
        ids.append(s.id)
    return ids


def set_ts(st: storemod.Store, sid: int, ts: float) -> None:
    st.db.execute("UPDATE snapshots SET ts=? WHERE id=?", (ts, sid))
    st.db.commit()


# ---- squash ----
def test_squash_chain(st: storemod.Store) -> None:
    ids = make_chain(st, 5)                       # 1..5
    keep, removed = st.squash(ids[1:4], "묶음")    # 2,3,4 → 4 만 남음
    assert keep.id == 4 and keep.kind == "label" and keep.message == "묶음"
    assert removed == 2
    assert [s.id for s in st.log()] == [5, 4, 1]
    # 지운 스냅샷의 tree 행이 없다
    assert st.db.execute("SELECT COUNT(*) FROM tree WHERE snapshot_id IN (2,3)").fetchone()[0] == 0
    # 부모 재연결: 4 의 부모는 1, 5 는 그대로 4
    assert st.get(4).parent == 1 and st.get(5).parent == 4
    assert len(st.tree(4)) == 1


def test_squash_not_contiguous(st: storemod.Store) -> None:
    ids = make_chain(st, 4)
    with pytest.raises(ValueError):
        st.squash([ids[0], ids[2]], "x")
    with pytest.raises(ValueError):
        st.squash([ids[0]], "x")
    assert len(st.log()) == 4


def test_squash_keeps_head(st: storemod.Store) -> None:
    ids = make_chain(st, 3)
    keep, removed = st.squash(ids, "전부")
    assert keep.id == 3 and removed == 2
    assert [s.id for s in st.log()] == [3]
    assert st.get(3).parent is None


# ---- prune ----
def test_prune_age_and_keep(st: storemod.Store) -> None:
    ids = make_chain(st, 6)                       # 1..6 auto
    now = time.time()
    for i, sid in enumerate(ids):
        set_ts(st, sid, now - (30 - i) * DAY)     # 1 이 가장 오래됨
    st.db.execute("UPDATE snapshots SET kind='label' WHERE id=2")
    st.db.commit()
    victims = st.prune(auto_days=14, keep_last_auto=2, now=now)
    # label(2)·최신 auto 2개(5,6)·HEAD(6) 는 보존
    assert victims == [1, 3, 4]
    assert [s.id for s in st.log()] == [6, 5, 2]
    assert st.get(2).parent is None and st.get(5).parent == 2


def test_prune_dry_run_and_zero(st: storemod.Store) -> None:
    ids = make_chain(st, 3)
    now = time.time()
    for sid in ids:
        set_ts(st, sid, now - 60 * DAY)
    assert st.prune(auto_days=14, keep_last_auto=0, now=now, dry_run=True) == [1, 2]
    assert len(st.log()) == 3                     # 드라이런은 아무것도 안 지움
    assert st.prune(auto_days=0, keep_last_auto=0, now=now) == []
    assert len(st.log()) == 3


def test_prune_keeps_recent(st: storemod.Store) -> None:
    ids = make_chain(st, 4)
    now = time.time()
    for sid in ids:
        set_ts(st, sid, now - 60 * DAY)
    assert st.prune(auto_days=14, keep_last_auto=30, now=now) == []


# ---- gc ----
def test_gc(st: storemod.Store) -> None:
    make_chain(st, 3)
    orphan = st.objects / "zz" / ("z" * 40)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"0123456789")
    tmp = st.objects / "zz" / "leftover.tmp"
    tmp.write_bytes(b"xx")
    n, size = st.gc(dry_run=True)
    assert n == 1 and size == 10 and orphan.exists()
    n, size = st.gc()
    assert (n, size) == (1, 10)
    assert not orphan.exists() and not tmp.exists() and not orphan.parent.exists()
    # 참조되는 객체는 남는다
    for e in st.tree(3).values():
        assert st.object_path(e.sha).exists()


def test_restore_after_delete(st: storemod.Store) -> None:
    ids = make_chain(st, 4)
    st.squash(ids[1:3], "묶음")                   # 2,3 → 3
    write(st, "A.uasset", b"dirty")
    plan = st.plan_restore(3)
    r = st.apply_restore(plan, check_editor=False)
    assert r.written == 1
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"A-v2"


# ---- web API ----
def test_api_squash(st: storemod.Store) -> None:
    ids = make_chain(st, 4)
    r = web.api_squash(st, ids[0:3], "묶음")
    assert r["snapshot"]["id"] == 3 and r["snapshot"]["kind"] == "label"
    assert r["removed"] == 2
    with pytest.raises(ValueError):
        web.api_squash(st, [3, 999], "x")


def test_api_prune(st: storemod.Store) -> None:
    ids = make_chain(st, 4)
    now = time.time()
    for sid in ids:
        set_ts(st, sid, now - 60 * DAY)
    st.cfg.keep_last_auto = 0
    r = web.api_prune(st, dry_run=True)
    assert r["ids"] == [1, 2, 3] and r["objects"] >= 3 and r["bytes"] > 0
    assert len(st.log()) == 4
    r2 = web.api_prune(st, dry_run=False)
    assert r2["ids"] == [1, 2, 3] and r2["objects"] == r["objects"]
    assert [s.id for s in st.log()] == [4]
