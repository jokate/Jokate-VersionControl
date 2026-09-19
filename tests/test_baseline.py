"""baseline(올린 것) 과 자동 기록(auto 스냅샷)의 분리."""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "Proj"
    (root / "Content" / "Foo").mkdir(parents=True)
    cfgmod.init(root)
    (root / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA-v1")
    (root / "Content" / "Foo" / "B.uasset").write_bytes(b"BBBB-v1")
    return root


def _st(project: Path) -> storemod.Store:
    return storemod.Store(cfgmod.load(project))


def test_auto_snaps_keep_status(project: Path) -> None:
    """자동 스냅샷이 여러 번 찍혀도 '올리지 않은 변경'은 비지 않는다."""
    st = _st(project)
    st.upload("처음 올림")
    assert st.status().empty
    (project / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")
    st.snap("")
    st.snap("", force=True)
    d = st.status()
    assert [n.rel for _, n in d.modified] == ["Foo/A.uasset"]
    st.close()


def test_upload_only_one(project: Path) -> None:
    content = project / "Content" / "Foo"
    st = _st(project)
    st.upload("처음 올림")
    content.joinpath("A.uasset").write_bytes(b"AAAA-v2")
    content.joinpath("B.uasset").write_bytes(b"BBBB-v2")
    snap, d, stored = st.upload("A만", only=["Foo\\A.uasset"])
    assert snap is not None and snap.kind == "label"
    assert [n.rel for _, n in d.modified] == ["Foo/A.uasset"]
    rest = st.status()
    assert [n.rel for _, n in rest.modified] == ["Foo/B.uasset"]
    # 스냅샷 트리는 전체 작업 트리 (B 의 최신본도 기록)
    t = st.tree(snap.id)
    assert set(t) == {"Foo/A.uasset", "Foo/B.uasset"}
    assert st.object_path(t["Foo/B.uasset"].sha).exists() and stored == 2
    # show 는 '이번에 올린 것'만
    _, shown = st.show(snap.id)
    assert [n.rel for _, n in shown.modified] == ["Foo/A.uasset"] and shown.empty is False
    # 선택분에 변경이 없으면 None
    assert st.upload("다시", only=["Foo/A.uasset"])[0] is None
    st.close()


def test_upload_creates_snapshot_even_if_tree_same(project: Path) -> None:
    st = _st(project)
    st.upload("처음 올림")
    (project / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")
    st.snap("")                       # auto 가 이미 전체 트리를 담았다
    n = len(st.fix_log())
    snap, d, _ = st.upload("올림")
    assert snap is not None and len(st.fix_log()) == n + 1
    assert st.journal() == []          # 확정하면 그 전의 작업 중 기록은 사라진다
    assert [n2.rel for _, n2 in d.modified] == ["Foo/A.uasset"]
    assert st.status().empty
    st.close()


def test_upload_delete_and_move(project: Path) -> None:
    content = project / "Content" / "Foo"
    st = _st(project)
    st.upload("처음 올림")
    content.joinpath("A.uasset").unlink()
    os.replace(content / "B.uasset", content / "B2.uasset")
    snap, d, _ = st.upload("삭제만", only=["Foo/A.uasset"])
    assert [e.rel for e in d.deleted] == ["Foo/A.uasset"] and not d.moved
    assert "Foo/A.uasset" not in st.baseline()
    # 이동은 old rel 만 골라도 쌍으로 처리
    snap2, d2, _ = st.upload("이동", only=["Foo/B.uasset"])
    assert [(o.rel, n.rel) for o, n in d2.moved] == [("Foo/B.uasset", "Foo/B2.uasset")]
    assert set(st.baseline()) == {"Foo/B2.uasset"}
    assert st.status().empty
    st.close()


def test_revert_to_baseline_clears_journal(project: Path) -> None:
    content = project / "Content" / "Foo"
    st = _st(project)
    st.upload("처음 올림")
    content.joinpath("A.uasset").write_bytes(b"AAAA-v2")
    content.joinpath("B.uasset").write_bytes(b"BBBB-v2")
    st.snap("")                         # 안전망 자동 기록
    r = st.revert_to_baseline(["Foo/A.uasset"], check_editor=False)
    assert content.joinpath("A.uasset").read_bytes() == b"AAAA-v1"
    assert content.joinpath("B.uasset").read_bytes() == b"BBBB-v2"
    # B 의 변경이 아직 남아 있으므로 작업 중 기록은 지우지 않는다(실행 취소 지점은 HEAD)
    assert r.written == 1 and r.undo is not None and r.cleared == 0
    assert r.undo.id in [s.id for s in st.journal()]
    d = st.status()
    assert [n.rel for _, n in d.modified] == ["Foo/B.uasset"]
    # 남은 변경까지 버리면 실행 취소 지점 하나만 남기고 정리된다
    r2 = st.revert_to_baseline(["Foo/B.uasset"], check_editor=False)
    assert r2.undo is not None and [s.id for s in st.journal()] == [r2.undo.id]
    assert [s.role for s in st.log()] == ["journal", "fix"]
    st.close()


def test_restore_leaves_pending_vs_baseline(project: Path) -> None:
    """롤백해도 baseline 은 그대로 → 결과가 baseline 과 다르면 pending 으로 보인다."""
    content = project / "Content" / "Foo"
    st = _st(project)
    st.upload("처음 올림")
    content.joinpath("A.uasset").write_bytes(b"AAAA-v2")
    s2, _, _ = st.upload("두번째")
    content.joinpath("A.uasset").write_bytes(b"AAAA-v3")
    st.snap("")
    st.apply_restore(st.plan_restore(1, ["Foo/A.uasset"]), check_editor=False)
    assert content.joinpath("A.uasset").read_bytes() == b"AAAA-v1"
    d = st.status()          # baseline 은 v2 → v1 은 '올리지 않은 변경'
    assert [n.rel for _, n in d.modified] == ["Foo/A.uasset"]
    st.close()


def test_migration_fills_baseline(project: Path) -> None:
    st = _st(project)
    st.snap("first", kind="label")      # 옛 방식 (baseline 갱신 없음)
    st.close()
    db = cfgmod.load(project).state_dir / "index.sqlite"
    con = sqlite3.connect(db)
    con.execute("DELETE FROM baseline")
    con.execute("DELETE FROM store_meta")
    con.commit()
    con.close()
    st2 = _st(project)
    assert set(st2.baseline()) == {"Foo/A.uasset", "Foo/B.uasset"}
    assert st2.status().empty
    st2.close()


def test_gc_keeps_baseline_objects(project: Path) -> None:
    st = _st(project)
    snap, _, _ = st.upload("처음 올림")
    sha = st.baseline()["Foo/A.uasset"].sha
    st.db.execute("DELETE FROM tree WHERE snapshot_id=?", (snap.id,))   # 트리 참조를 없애도
    st.db.commit()
    st.gc()
    assert st.object_path(sha).exists()
    st.close()


def test_squash_merges_uploaded(project: Path) -> None:
    content = project / "Content" / "Foo"
    st = _st(project)
    st.upload("처음 올림")
    content.joinpath("A.uasset").write_bytes(b"AAAA-v2")
    s2, _, _ = st.upload("두번째")
    content.joinpath("A.uasset").write_bytes(b"AAAA-v3")
    content.joinpath("C.uasset").write_bytes(b"CCCC")
    s3, _, _ = st.upload("세번째")
    kept, removed = st.squash([s2.id, s3.id], "묶음", include_labels=True)
    assert removed == 1
    _, d = st.show(kept.id)
    mod = {n.rel: (o.sha, n.sha) for o, n in d.modified}
    assert list(mod) == ["Foo/A.uasset"] and [e.rel for e in d.added] == ["Foo/C.uasset"]
    first_old, last_new = mod["Foo/A.uasset"]
    assert first_old == st.tree(1)["Foo/A.uasset"].sha
    assert last_new == st.baseline()["Foo/A.uasset"].sha
    st.close()
