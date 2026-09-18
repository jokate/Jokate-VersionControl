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
    assert log[0]["counts"] == {"added": 1, "modified": 1, "resave": 0, "moved": 1, "deleted": 0}
    assert log[1]["counts"] == {"added": 2, "modified": 0, "resave": 0, "moved": 0, "deleted": 0}
    assert log[0]["all_noise"] is False and log[1]["all_noise"] is False
    assert log[0]["total"] == 3 and "time" in log[0]
    assert "?" in log[0]["by_class"]


def test_api_snap(st: storemod.Store) -> None:
    r = web.api_snap(st, 2)
    assert r["snapshot"]["id"] == 2 and r["snapshot"]["parent"] == 1
    d = r["diff"]
    assert [x["new"]["rel"] for x in d["modified"]] == ["Foo/A.uasset"]
    assert d["modified"][0]["new"]["noise"] is False and d["all_noise"] is False
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
    assert r["diff"]["counts"] == {"added": 0, "modified": 1, "resave": 0, "moved": 1, "deleted": 1}
    assert r["broken"] == [] and r["dependents"] == []
    # 드라이런: 파일·헤드 변화 없음
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"
    assert st.head().id == 2
    one = web.api_restore(st, 1, ["Foo/A.uasset"])
    assert one["diff"]["counts"] == {"added": 0, "modified": 1, "resave": 0, "moved": 0, "deleted": 0}
    with pytest.raises(KeyError):
        web.api_restore(st, 1, ["Nope.uasset"])


FIXTURE = Path(__file__).parent / "fixtures" / "IA_Aim.uasset"


def test_api_noise_serialization(tmp_path: Path) -> None:
    """리세이브만 스냅샷: api_log 의 all_noise/counts.resave, api_snap 의 entry.noise."""
    if not FIXTURE.exists():
        pytest.skip(f"픽스처 없음: {FIXTURE}")
    data = FIXTURE.read_bytes()
    root = tmp_path / "Proj"
    (root / "Content" / "Foo").mkdir(parents=True)
    cfgmod.init(root)
    asset = root / "Content" / "Foo" / "IA_Aim.uasset"
    asset.write_bytes(data)
    st = storemod.Store(cfgmod.load(root))
    st.snap("first")
    b = bytearray(data)
    for i in range(24, 44):  # SavedHash 20B 만 뒤집기 → 헤더만 변경
        b[i] ^= 0xFF
    asset.write_bytes(bytes(b))
    st.snap("")
    log = web.api_log(st)
    assert log[0]["id"] == 2 and log[0]["all_noise"] is True
    assert log[0]["counts"]["resave"] == 1 and log[0]["counts"]["modified"] == 0
    assert log[1]["all_noise"] is False
    d = web.api_snap(st, 2)["diff"]
    assert d["all_noise"] is True and d["counts"]["resave"] == 1
    assert d["modified"][0]["new"]["noise"] is True
    assert any(c.get("resave") == 1 for c in d["by_class"].values())
    assert web.api_status(st)["diff"]["all_noise"] is False


def test_api_snap_create(st: storemod.Store) -> None:
    r = web.api_snap_create(st, "라벨")
    assert r["snapshot"]["id"] == 3 and r["snapshot"]["kind"] == "label"
    assert r["diff"]["counts"] == {"added": 0, "modified": 0, "resave": 0, "moved": 0, "deleted": 0}
    assert web.api_log(st)[0]["message"] == "라벨"


def test_api_thumb(st: storemod.Store) -> None:
    web._thumb_cache.clear()
    sha = st.tree(2)["Foo/A.uasset"].sha
    assert web.api_thumb(st, sha) is None  # 가짜 바이너리 → 파싱 실패 → 없음
    assert sha in web._thumb_cache  # 없음도 캐시
    assert web.api_thumb(st, "zz") is None and web.api_thumb(st, "") is None
    assert web.api_thumb(st, "0" * 64) is None  # 객체 없음


def test_api_search(st: storemod.Store) -> None:
    # 애셋 이름: A 는 #1 추가 · #2 수정 → 둘 다 일치 (최신순)
    r = web.api_search(st, "a.uasset")
    assert [x["id"] for x in r["snapshots"]] == [2, 1] and r["q"] == "a.uasset"
    assert "Foo/A.uasset" in r["snapshots"][0]["matched"] and r["snapshots"][0]["by"] == "asset"
    # 대소문자 무시
    assert [x["id"] for x in web.api_search(st, "FOO/B")["snapshots"]] == [2]
    assert [x["id"] for x in web.api_search(st, "foo/b")["snapshots"]] == [2]
    # 메시지
    m = web.api_search(st, "fir")["snapshots"]
    assert [x["id"] for x in m] == [1] and m[0]["by"] == "message" and m[0]["matched"] == []
    # 클래스(가짜 바이너리는 '?')
    c = web.api_search(st, "?")["snapshots"]
    assert [x["id"] for x in c] == [2, 1] and c[0]["by"] == "class"
    # 빈 q
    assert web.api_search(st, "")["snapshots"] == [] and web.api_search(st, "   ")["q"] == ""
    # 없는 것
    assert web.api_search(st, "zzz")["snapshots"] == []
    # 변경 안 된 애셋은 그 스냅샷에서 일치하지 않음
    (st.cfg.content / "Foo" / "A.uasset").write_bytes(b"AAAA-v3")
    st.snap("third")
    assert [x["id"] for x in web.api_search(st, "b.uasset")["snapshots"]] == [2]
    assert [x["id"] for x in web.api_search(st, "a.uasset")["snapshots"]] == [3, 2, 1]
    assert len(web.api_search(st, "a.uasset", limit=2)["snapshots"]) == 2


def test_api_thumb_rel(st: storemod.Store) -> None:
    web._thumb_cache.clear()
    assert web.api_thumb(st, rel="../../etc/passwd.uasset") is None
    assert web.api_thumb(st, rel="Foo/../../x.uasset") is None
    assert web._thumb_cache == {}                       # 탈출은 캐시도 안 함
    assert web.api_thumb(st, rel="Nope.uasset") is None  # 파일 없음
    assert web.api_thumb(st, rel="Foo/A.uasset") is None  # 가짜 바이너리 → 썸네일 없음
    assert len(web._thumb_cache) == 1                   # rel+mtime+size 키로 캐시
    assert next(iter(web._thumb_cache)).startswith("rel:")


def test_api_status_and_snap_only(st: storemod.Store) -> None:
    assert web.api_status(st)["diff"]["counts"] == {"added": 0, "modified": 0, "resave": 0, "moved": 0, "deleted": 0}
    foo = st.cfg.content / "Foo"
    (foo / "A.uasset").write_bytes(b"AAAA-v3")
    (foo / "D.uasset").write_bytes(b"DDDD")
    d = web.api_status(st)["diff"]
    assert d["counts"] == {"added": 1, "modified": 1, "resave": 0, "moved": 0, "deleted": 0}
    r = web.api_snap_create(st, "부분", ["Foo/A.uasset"])
    assert r["snapshot"]["id"] == 3 and r["stored"] == 1
    assert r["diff"]["counts"] == {"added": 0, "modified": 1, "resave": 0, "moved": 0, "deleted": 0}
    assert set(st.tree(3)) == {"Foo/A.uasset", "Foo/B.uasset", "Foo/C2.uasset"}
    assert web.api_status(st)["diff"]["counts"]["added"] == 1   # D 는 아직 안 올라감
    assert web.api_snap_create(st, "무변경", ["Foo/A.uasset"])["snapshot"] is None
    with pytest.raises(KeyError):
        web.api_snap_create(st, "x", ["Nope.uasset"])


def test_api_restore_apply(st: storemod.Store, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storemod, "editor_running", lambda: False)
    r = web.api_restore_apply(st, 1, ["Foo/A.uasset"], False)
    assert r["ok"] is True and r["safety"]["id"] == 3 and r["result"]["id"] == 4
    assert r["written"] == 1 and r["deleted"] == 0 and r["reloaded"] is None
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v1"
    assert (st.cfg.content / "Foo" / "B.uasset").exists()   # 나머지는 유지
    with pytest.raises(KeyError):
        web.api_restore_apply(st, 99)


def test_api_restore_apply_blocked(st: storemod.Store, monkeypatch: pytest.MonkeyPatch) -> None:
    from jokate import bridge
    monkeypatch.setattr(storemod, "editor_running", lambda: True)
    monkeypatch.setattr(bridge, "bridge_alive", lambda cfg, *a, **k: True)
    monkeypatch.setattr(bridge, "request", lambda cfg, op, pkgs, args=None, **k: {"ok": True, "dirty": list(pkgs)})
    with pytest.raises(storemod.RestoreBlocked) as ei:
        web.api_restore_apply(st, 1, ["Foo/A.uasset"], False)
    assert ei.value.dirty == ["/Game/Foo/A"]
    code, body = web.error_response(ei.value)
    assert code == 409 and body["ok"] is False and body["dirty"] == ["/Game/Foo/A"] and "저장 안 된" in body["error"]
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2" and st.head().id == 2
    # 브릿지 없음도 409, dirty 는 빈 목록
    monkeypatch.setattr(bridge, "bridge_alive", lambda cfg, *a, **k: False)
    with pytest.raises(storemod.RestoreBlocked) as ei2:
        web.api_restore_apply(st, 1)
    assert web.error_response(ei2.value)[0] == 409 and ei2.value.dirty == []
    # 그 외 매핑
    assert web.error_response(KeyError("x"))[0] == 404
    assert web.error_response(ValueError("x"))[0] == 400
    assert web.error_response(RuntimeError("x"))[0] == 500
    code, body = web.error_response(FileNotFoundError("객체 없음: Foo/A.uasset (abc)"))
    assert code == 409 and body["ok"] is False and "객체 없음" in body["error"]
