"""21a단계: '확정' 중심 모델 — fix(확정 버전) / journal(작업 중 기록)."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import __main__ as cli  # noqa: E402
from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402
from jokate import web  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "Proj"
    foo = root / "Content" / "Foo"
    foo.mkdir(parents=True)
    (foo / "A.uasset").write_bytes(b"AAAA-v1")
    (foo / "B.uasset").write_bytes(b"BBBB-v1")
    cfgmod.init(root)
    return root


def _st(project: Path) -> storemod.Store:
    return storemod.Store(cfgmod.load(project))


def write(st: storemod.Store, name: str, data: bytes) -> None:
    (st.cfg.content / "Foo" / name).write_bytes(data)


# ---- 마이그레이션 ----
def test_migration_classifies_roles(project: Path) -> None:
    cfg = cfgmod.load(project)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(cfg.state_dir / "index.sqlite")
    db.executescript("""
    CREATE TABLE snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, parent INTEGER,
        kind TEXT NOT NULL CHECK(kind IN ('auto','label')), message TEXT NOT NULL DEFAULT '',
        ts REAL NOT NULL, uploaded TEXT NOT NULL DEFAULT '');
    CREATE TABLE tree(snapshot_id INTEGER NOT NULL, rel TEXT NOT NULL, sha TEXT NOT NULL,
        size INTEGER NOT NULL, cls TEXT NOT NULL DEFAULT '', deps TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(snapshot_id, rel));
    INSERT INTO snapshots(parent,kind,message,ts,uploaded) VALUES(NULL,'label','옛 수동',1.0,'');
    INSERT INTO snapshots(parent,kind,message,ts,uploaded) VALUES(1,'auto','',2.0,'');
    INSERT INTO snapshots(parent,kind,message,ts,uploaded) VALUES(2,'label','올림',3.0,'[]x');
    INSERT INTO snapshots(parent,kind,message,ts,uploaded) VALUES(3,'label','롤백: #1',4.0,'');
    """)
    db.commit()
    db.close()
    st = _st(project)
    roles = {s.id: s.role for s in st.log()}
    assert roles == {1: "fix", 2: "journal", 3: "fix", 4: "journal"}
    assert [s.id for s in st.fix_log()] == [3, 1] and [s.id for s in st.journal()] == [4, 2]
    assert st.head_fix().id == 3
    st.close()


def test_migration_promotes_oldest_label_when_no_fix(project: Path) -> None:
    cfg = cfgmod.load(project)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(cfg.state_dir / "index.sqlite")
    db.executescript("""
    CREATE TABLE snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, parent INTEGER,
        kind TEXT NOT NULL CHECK(kind IN ('auto','label')), message TEXT NOT NULL DEFAULT '',
        ts REAL NOT NULL, uploaded TEXT NOT NULL DEFAULT '');
    CREATE TABLE tree(snapshot_id INTEGER NOT NULL, rel TEXT NOT NULL, sha TEXT NOT NULL,
        size INTEGER NOT NULL, cls TEXT NOT NULL DEFAULT '', deps TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(snapshot_id, rel));
    INSERT INTO snapshots(parent,kind,message,ts,uploaded) VALUES(NULL,'auto','',1.0,'');
    INSERT INTO snapshots(parent,kind,message,ts,uploaded) VALUES(1,'label','롤백: #1',2.0,'');
    INSERT INTO snapshots(parent,kind,message,ts,uploaded) VALUES(2,'label','롤백: #2',3.0,'');
    """)
    db.commit()
    db.close()
    st = _st(project)
    assert {s.id: s.role for s in st.log()} == {1: "journal", 2: "fix", 3: "journal"}
    st.close()


# ---- 확정 ----
def test_confirm_clears_journal_and_gc(project: Path) -> None:
    st = _st(project)
    st.confirm("처음 확정")
    for i in range(3):                       # 자동 저장 3개
        write(st, "A.uasset", f"AAAA-auto{i}".encode())
        st.snap("")
    st.apply_restore(st.plan_restore(1, ["Foo/A.uasset"]), check_editor=False)   # 롤백 1회
    assert len(st.journal()) >= 4            # 자동 3 + 롤백 안전/결과
    orphan_shas = [st.tree(s.id)["Foo/A.uasset"].sha for s in st.journal()]
    write(st, "A.uasset", b"AAAA-final")
    r = st.confirm("두번째 확정")
    assert r.cleared >= 4 and st.journal() == []
    fixes = st.fix_log()
    assert [s.message for s in fixes] == ["두번째 확정", "처음 확정"]
    assert [s.role for s in st.log()] == ["fix", "fix"]
    # 지워진 작업 중 기록만 가리키던 객체는 GC 된다
    live = {e.sha for s in st.log() for e in st.tree(s.id).values()} | {e.sha for e in st.baseline().values()}
    for sha in orphan_shas:
        if sha not in live:
            assert not st.object_path(sha).exists()
    assert st.status().empty
    st.close()


def test_confirm_partial_keeps_rest_pending(project: Path) -> None:
    st = _st(project)
    st.confirm("처음 확정")
    write(st, "A.uasset", b"AAAA-v2")
    write(st, "B.uasset", b"BBBB-v2")
    st.snap("")
    r = st.confirm("A 만 확정", only=["Foo/A.uasset"])
    snap, d, _ = r
    assert snap is not None and snap.role == "fix"
    assert [n.rel for _, n in d.modified] == ["Foo/A.uasset"]
    assert st.journal() == []                                  # 부분 확정이어도 전부 정리
    rest = st.status()
    assert [n.rel for _, n in rest.modified] == ["Foo/B.uasset"]   # B 는 확정 안 된 변경으로 남는다
    assert (st.cfg.content / "Foo" / "B.uasset").read_bytes() == b"BBBB-v2"   # 디스크 내용 보존
    assert st.tree(snap.id)["Foo/B.uasset"].sha == rest.modified[0][1].sha    # 새 fix 트리에도 있다
    st.close()


def test_confirm_without_pending_changes_nothing(project: Path) -> None:
    st = _st(project)
    st.confirm("처음 확정")
    st.snap("")                     # 변경 없음 → 아무것도 안 생김
    write(st, "A.uasset", b"AAAA-v2")
    st.snap("")
    n = len(st.journal())
    r = st.confirm("변경 없는 것만", only=["Foo/B.uasset"])
    assert r[0] is None and r.cleared == 0 and len(st.journal()) == n
    st.close()


def test_restore_to_past_fix_then_confirm(project: Path) -> None:
    st = _st(project)
    st.confirm("v1")
    write(st, "A.uasset", b"AAAA-v2")
    st.confirm("v2")
    # 확정 뒤에도 과거 확정 버전으로 되돌릴 수 있다
    r = st.apply_restore(st.plan_restore(1, ["Foo/A.uasset"]), check_editor=False)
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v1"
    assert r.result.role == "journal" and r.result.kind == "auto"
    d = st.status()
    assert [n.rel for _, n in d.modified] == ["Foo/A.uasset"]   # 확정 안 된 변경으로 보인다
    snap, _, _ = st.confirm("되돌린 v1 확정")
    assert snap.role == "fix" and st.journal() == []
    assert [s.message for s in st.fix_log()] == ["되돌린 v1 확정", "v2", "v1"]
    st.close()


# ---- 변경 버리기 ----
def test_discard_leaves_single_undo_point(project: Path) -> None:
    st = _st(project)
    st.confirm("처음 확정")
    write(st, "A.uasset", b"AAAA-v2")
    st.snap("")
    write(st, "A.uasset", b"AAAA-v3")
    r = st.revert_to_baseline(["Foo/A.uasset"], check_editor=False)
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v1"
    assert r.undo is not None and r.undo.role == "journal"
    assert [s.id for s in st.journal()] == [r.undo.id]          # 실행 취소 지점 하나만
    # 그 지점으로 되돌리면 버리기 전 내용이 돌아온다
    st.apply_restore(st.plan_restore(r.undo.id, ["Foo/A.uasset"]), check_editor=False)
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v3"
    # 다음 확정에서 그 지점도 사라진다
    st.confirm("다시 확정")
    assert st.journal() == [] and [s.role for s in st.log()] == ["fix", "fix"]
    st.close()


def test_discard_without_new_safety_has_no_undo(project: Path) -> None:
    st = _st(project)
    st.confirm("처음 확정")
    write(st, "A.uasset", b"AAAA-v2")
    st.snap("")                      # HEAD 가 곧 되돌리기 직전 상태
    r = st.revert_to_baseline(["Foo/A.uasset"], check_editor=False)
    assert r.undo is None and r.safety_created is False
    assert st.journal() == [] and r.cleared >= 1
    st.close()


# ---- 보관 규칙 ----
def test_prune_never_touches_fix(project: Path) -> None:
    st = _st(project)
    st.confirm("확정1")
    for i in range(3):
        write(st, "A.uasset", f"A{i}".encode())
        st.snap("")
    now = 10_000_000.0
    st.db.execute("UPDATE snapshots SET ts=?", (now - 100 * 86400,))
    st.db.commit()
    victims = st.prune(auto_days=14, keep_last_auto=0, now=now)
    assert 1 not in victims
    assert st.get(1).role == "fix"
    assert all(st.get(v) is None or st.get(v).role == "journal" for v in victims)
    st.close()


def test_squash_protects_fixes(project: Path) -> None:
    st = _st(project)
    st.confirm("확정1")
    write(st, "A.uasset", b"A2")
    st.confirm("확정2")
    write(st, "A.uasset", b"A3")
    st.snap("")                       # journal (#3)
    write(st, "A.uasset", b"A4")
    st.confirm("확정3")               # journal 은 여기서 정리된다
    ids = [s.id for s in sorted(st.log(), key=lambda s: s.id)]
    with pytest.raises(storemod.SquashHasLabels) as ei:
        st.squash(ids, "묶음")
    assert "확정 버전" in str(ei.value)
    keep, removed = st.squash(ids, "묶음", include_labels=True)
    assert keep.role == "fix" and removed == len(ids) - 1
    st.close()


# ---- CLI · API ----
def test_cli_confirm_discard_log(project: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["confirm", str(project), "-m", "처음 확정"]) == 0
    assert "확정 →" in capsys.readouterr().out
    (project / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")
    assert cli.main(["snap", str(project)]) == 0
    capsys.readouterr()
    assert cli.main(["log", str(project)]) == 0
    out = capsys.readouterr().out
    assert "처음 확정" in out and "확정" in out and out.count("\n") == 1     # 확정 버전만
    assert cli.main(["log", str(project), "--all"]) == 0
    out_all = capsys.readouterr().out
    assert out_all.count("\n") == 2 and "    #" in out_all                  # 작업 중 기록은 들여씀
    assert cli.main(["status", str(project)]) == 0
    assert "확정 안 된 변경" in capsys.readouterr().out
    assert cli.main(["discard", str(project), "--apply"]) == 0
    assert "변경 버림" in capsys.readouterr().out
    assert (project / "Content" / "Foo" / "A.uasset").read_bytes() == b"AAAA-v1"


def test_api_confirm_discard(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storemod, "editor_running", lambda *a, **k: False)
    st = _st(project)
    r = web.api_confirm(st, "처음 확정")
    assert r["snapshot"]["role"] == "fix" and r["cleared"] == 0
    write(st, "A.uasset", b"AAAA-v2")
    st.snap("")
    info = web.api_info(st)
    assert info["fixes"] == 1 and info["journal"] == 1 and info["pending"] == 1
    assert [x["id"] for x in web.api_log(st, "fix")] == [1]
    assert [x["id"] for x in web.api_log(st, "journal")] == [2]
    assert len(web.api_log(st, "all")) == 2 and len(web.api_log(st)) == 2
    write(st, "A.uasset", b"AAAA-v3")
    d = web.api_discard_apply(st, ["Foo/A.uasset"])
    assert d["ok"] and d["undo"] and d["undo"]["role"] == "journal" and d["cleared"] >= 1
    assert web.api_info(st)["journal"] == 1
    r2 = web.api_confirm(st, "빈 확정")
    assert r2["snapshot"] is None and r2["cleared"] == 0
    st.close()
