"""snap → 수정 → snap → restore 1 --apply 흐름 검증. 가짜 바이너리로 충분(파서 실패는 error 필드에만 남고 sha 는 계산됨)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import __main__ as cli  # noqa: E402
from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "Proj"
    (root / "Content" / "Foo").mkdir(parents=True)
    cfgmod.init(root)
    (root / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA-v1")
    (root / "Content" / "Foo" / "C.uasset").write_bytes(b"CCCC")
    return root


def test_restore_roundtrip(project: Path) -> None:
    content = project / "Content"
    st = storemod.Store(cfgmod.load(project))
    s1, _, _ = st.snap("first")
    assert s1 is not None and s1.id == 1

    # 수정 + 추가 + 삭제 + 이동
    (content / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")
    (content / "Foo" / "B.uasset").write_bytes(b"BBBB")
    os.replace(content / "Foo" / "C.uasset", content / "Foo" / "C2.uasset")
    s2, d, _ = st.snap("second")
    assert s2 is not None and s2.id == 2

    plan = st.plan_restore(1)
    assert [n.rel for _, n in plan.diff.modified] == ["Foo/A.uasset"]
    assert [e.rel for e in plan.diff.deleted] == ["Foo/B.uasset"]
    assert [(o.rel, n.rel) for o, n in plan.diff.moved] == [("Foo/C2.uasset", "Foo/C.uasset")]
    assert not plan.broken and not plan.dependents
    text = storemod.format_restore(plan)
    assert "수정 되돌림" in text and "삭제" in text and "이동" in text
    # 드라이런은 아무것도 바꾸지 않는다
    assert (content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"
    assert st.head().id == 2

    r = st.apply_restore(plan, check_editor=False)
    assert (content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v1"
    assert not (content / "Foo" / "B.uasset").exists()
    assert not (content / "Foo" / "C2.uasset").exists()
    assert (content / "Foo" / "C.uasset").read_bytes() == b"CCCC"
    assert not list(content.rglob("*.jokate-tmp"))
    assert r.written == 2 and r.deleted == 2
    # 안전 스냅샷(auto, 롤백 직전) + 결과 스냅샷(label, 롤백)
    assert r.safety.id == 3 and r.safety.kind == "auto" and r.safety.message == "롤백 직전 #1"
    assert r.result.id == 4 and r.result.kind == "label" and r.result.message == "롤백: #1"
    assert {e.sha for e in st.tree(3).values()} == {e.sha for e in st.tree(2).values()}
    assert {(e.rel, e.sha) for e in st.tree(4).values()} == {(e.rel, e.sha) for e in st.tree(1).values()}
    # 안전 스냅샷으로 다시 되돌리면 원상복구
    st.apply_restore(st.plan_restore(3), check_editor=False)
    assert (content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"
    assert (content / "Foo" / "B.uasset").exists()
    st.close()


def test_restore_single_asset(project: Path) -> None:
    content = project / "Content"
    st = storemod.Store(cfgmod.load(project))
    st.snap("first")
    (content / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")
    (content / "Foo" / "C.uasset").write_bytes(b"CCCC-v2")
    st.snap("second")
    plan = st.plan_restore(1, ["Foo\\A.uasset"])
    assert [n.rel for _, n in plan.diff.modified] == ["Foo/A.uasset"]
    st.apply_restore(plan, check_editor=False)
    assert (content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v1"
    assert (content / "Foo" / "C.uasset").read_bytes() == b"CCCC-v2"   # 나머지는 유지
    with pytest.raises(KeyError):
        st.plan_restore(1, ["Foo/Nope.uasset"])
    st.close()


def test_restore_ref_check(project: Path) -> None:
    """결과 트리 항목의 deps 가 사라지는 애셋/없는 패키지를 가리키면 경고."""
    st = storemod.Store(cfgmod.load(project))
    s1, _, _ = st.snap("first")
    st.db.execute("UPDATE tree SET deps=? WHERE snapshot_id=? AND rel=?",
                  ('["/Game/Foo/C", "/Game/Missing/X", "/Script/Engine"]', s1.id, "Foo/A.uasset"))
    st.db.commit()
    (project / "Content" / "Foo" / "C.uasset").unlink()
    (project / "Content" / "Foo" / "New.uasset").write_bytes(b"NEW")
    st.snap("second")
    # 전체 롤백: C 는 부활하므로 해결, Missing/X 만 깨짐
    plan = st.plan_restore(1)
    assert plan.broken == [("Foo/A.uasset", "/Game/Missing/X")]
    assert plan.dependents == []
    # New 만 되돌리기(=삭제)는 참조 문제 없음 (A 의 현재 deps 는 비어 있음)
    plan = st.plan_restore(1, ["Foo/New.uasset"])
    assert [e.rel for e in plan.diff.deleted] == ["Foo/New.uasset"]
    st.close()


def test_cli_dry_run(project: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["snap", str(project), "-m", "first"]) == 0
    (project / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")
    assert cli.main(["snap", str(project)]) == 0
    assert cli.main(["restore", str(project), "1"]) == 0
    out = capsys.readouterr().out
    assert "드라이런" in out and "Foo/A.uasset" in out
    assert (project / "Content" / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"
    assert cli.main(["restore", str(project), "99"]) == 1


def test_partial_snap_and_status(project: Path) -> None:
    """only 로 지정한 rel 만 반영, 나머지는 HEAD 유지, 객체 저장은 갱신된 것만."""
    content = project / "Content" / "Foo"
    st = storemod.Store(cfgmod.load(project))
    st.snap("first")
    assert st.status().empty
    (content / "A.uasset").write_bytes(b"AAAA-v2")
    (content / "B.uasset").write_bytes(b"BBBB")
    (content / "C.uasset").unlink()
    d = st.status()
    assert [n.rel for _, n in d.modified] == ["Foo/A.uasset"]
    assert [e.rel for e in d.added] == ["Foo/B.uasset"] and [e.rel for e in d.deleted] == ["Foo/C.uasset"]

    s2, d2, stored = st.snap("A만", only=["Foo\\A.uasset"])
    assert s2 is not None and stored == 1
    assert [n.rel for _, n in d2.modified] == ["Foo/A.uasset"] and not d2.added and not d2.deleted
    t2 = st.tree(s2.id)
    assert set(t2) == {"Foo/A.uasset", "Foo/C.uasset"}          # B 미반영, C 는 HEAD 유지
    assert t2["Foo/C.uasset"].sha == st.tree(1)["Foo/C.uasset"].sha
    # 선택한 것에 변경 없으면 생성 안 함
    assert st.snap("again", only=["Foo/A.uasset"])[0] is None
    # 디스크에 없는 rel 을 only 로 → 트리에서 제거, 객체 저장 0
    s3, d3, stored3 = st.snap("C삭제", only=["Foo/C.uasset"])
    assert stored3 == 0 and [e.rel for e in d3.deleted] == ["Foo/C.uasset"]
    assert set(st.tree(s3.id)) == {"Foo/A.uasset"}
    rest = st.status()
    assert [e.rel for e in rest.added] == ["Foo/B.uasset"] and not rest.modified and not rest.deleted
    with pytest.raises(KeyError):
        st.snap("x", only=["Foo/Nope.uasset"])
    st.close()


def test_cli_status_and_snap_only(project: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["snap", str(project), "-m", "first"]) == 0
    assert cli.main(["status", str(project)]) == 0
    assert "올릴 변경 없음" in capsys.readouterr().out
    (project / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")
    (project / "Content" / "Foo" / "B.uasset").write_bytes(b"BBBB")
    assert cli.main(["status", str(project)]) == 0
    out = capsys.readouterr().out
    assert "M Foo/A.uasset" in out and "A Foo/B.uasset" in out
    assert cli.main(["snap", str(project), "-m", "A만", "--only", "Foo/A.uasset"]) == 0
    assert "M Foo/A.uasset" in capsys.readouterr().out
    assert cli.main(["status", str(project)]) == 0
    out = capsys.readouterr().out
    assert "A Foo/B.uasset" in out and "M Foo/A.uasset" not in out
    assert cli.main(["snap", str(project), "--only", "Foo/Nope.uasset"]) == 1
