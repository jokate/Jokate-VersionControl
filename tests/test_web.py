"""웹 API 로직(api_*) 단위 테스트. 서버는 띄우지 않는다."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402
from jokate import web  # noqa: E402


@pytest.fixture
def st(tmp_path: Path) -> storemod.Store:
    root = tmp_path / "Proj"
    foo = root / "Content" / "Foo"
    foo.mkdir(parents=True)
    cfgmod.init(root)
    (foo / "A.uasset").write_bytes(b"AAAA-v1")
    (foo / "C.uasset").write_bytes(b"CCCC")
    s = storemod.Store(cfgmod.load(root))
    s.snap("first")
    (foo / "A.uasset").write_bytes(b"AAAA-v2")
    (foo / "B.uasset").write_bytes(b"BBBB")
    os.replace(foo / "C.uasset", foo / "C2.uasset")
    s.snap("")  # auto
    return s


def test_api_log(st: storemod.Store) -> None:
    log = web.api_log(st)
    assert [x["id"] for x in log] == [2, 1]
    assert log[0]["kind"] == "auto" and log[1]["kind"] == "label"
    assert log[0]["counts"] == {"added": 1, "modified": 1, "moved": 1, "deleted": 0}
    assert log[1]["counts"] == {"added": 2, "modified": 0, "moved": 0, "deleted": 0}
    assert log[0]["total"] == 3 and "time" in log[0]
    assert "?" in log[0]["by_class"]


def test_api_snap(st: storemod.Store) -> None:
    r = web.api_snap(st, 2)
    assert r["snapshot"]["id"] == 2 and r["snapshot"]["parent"] == 1
    d = r["diff"]
    assert [x["new"]["rel"] for x in d["modified"]] == ["Foo/A.uasset"]
    assert [x["rel"] for x in d["added"]] == ["Foo/B.uasset"]
    assert [(x["old"]["rel"], x["new"]["rel"]) for x in d["moved"]] == [("Foo/C.uasset", "Foo/C2.uasset")]
    assert d["counts"]["deleted"] == 0
    with pytest.raises(KeyError):
        web.api_snap(st, 99)


def test_api_asset(st: storemod.Store) -> None:
    r = web.api_asset(st, "Foo\\A.uasset")
    assert r["rel"] == "Foo/A.uasset"
    v = r["versions"]
    assert [x["id"] for x in v] == [2, 1]  # 최신순
    assert v[0]["state"] == "modified" and v[0]["changed"]
    assert v[1]["state"] == "added" and v[1]["changed"]
    assert v[0]["sha"] != v[1]["sha"] and v[0]["size"] == 7
    # 이동으로 사라진 경로: 삭제 항목이 남는다
    c = web.api_asset(st, "Foo/C.uasset")["versions"]
    assert [x["state"] for x in c] == ["deleted", "added"] and c[0]["sha"] is None
    assert web.api_asset(st, "Nope.uasset")["versions"] == []


def test_api_restore_dry_run(st: storemod.Store) -> None:
    r = web.api_restore(st, 1)
    assert r["snapshot"]["id"] == 1
    assert r["diff"]["counts"] == {"added": 0, "modified": 1, "moved": 1, "deleted": 1}
    assert r["broken"] == [] and r["dependents"] == []
    # 드라이런: 파일·헤드 변화 없음
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"
    assert st.head().id == 2
    one = web.api_restore(st, 1, ["Foo/A.uasset"])
    assert one["diff"]["counts"] == {"added": 0, "modified": 1, "moved": 0, "deleted": 0}
    with pytest.raises(KeyError):
        web.api_restore(st, 1, ["Nope.uasset"])


def test_api_snap_create(st: storemod.Store) -> None:
    r = web.api_snap_create(st, "라벨")
    assert r["snapshot"]["id"] == 3 and r["snapshot"]["kind"] == "label"
    assert r["diff"]["counts"] == {"added": 0, "modified": 0, "moved": 0, "deleted": 0}
    assert web.api_log(st)[0]["message"] == "라벨"


def test_api_thumb(st: storemod.Store) -> None:
    web._thumb_cache.clear()
    sha = st.tree(2)["Foo/A.uasset"].sha
    assert web.api_thumb(st, sha) is None  # 가짜 바이너리 → 파싱 실패 → 없음
    assert sha in web._thumb_cache  # 없음도 캐시
    assert web.api_thumb(st, "zz") is None and web.api_thumb(st, "") is None
    assert web.api_thumb(st, "0" * 64) is None  # 객체 없음
