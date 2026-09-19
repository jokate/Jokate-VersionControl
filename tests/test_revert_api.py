"""19b: baseline 모델의 웹 API(revert·pending·uploaded 직렬화)와 프로젝트별 editor_running."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jokate" / "ue"))

from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402
from jokate import web  # noqa: E402

import jokate_client as jc  # noqa: E402


@pytest.fixture
def st(tmp_path: Path) -> storemod.Store:
    root = tmp_path / "Proj"
    foo = root / "Content" / "Foo"
    foo.mkdir(parents=True)
    cfgmod.init(root)
    (foo / "A.uasset").write_bytes(b"AAAA-v1")
    s = storemod.Store(cfgmod.load(root))
    web.api_snap_create(s, "처음 올림")          # #1 label(upload)
    (foo / "A.uasset").write_bytes(b"AAAA-v2")
    (foo / "B.uasset").write_bytes(b"BBBB")
    s.snap("")                                   # #2 auto — baseline 은 그대로
    return s


def test_api_status_shows_pending_after_auto(st: storemod.Store) -> None:
    c = web.api_status(st)["diff"]["counts"]
    assert c["modified"] == 1 and c["added"] == 1     # 자동 스냅샷 뒤에도 남는다
    assert web.api_info(st)["pending"] == 2
    assert web.api_info(st)["last_label"]["id"] == 1


def test_api_snap_only_uploads_selected(st: storemod.Store) -> None:
    r = web.api_snap_create(st, "A 만 올림", ["Foo/A.uasset"])
    assert r["snapshot"]["kind"] == "label"
    assert r["diff"]["counts"] == {"added": 0, "modified": 1, "resave": 0, "moved": 0, "deleted": 0}
    assert web.api_status(st)["diff"]["counts"]["added"] == 1   # B 는 아직 안 올림
    assert web.api_info(st)["pending"] == 1


def test_api_log_serializes_uploaded(st: storemod.Store) -> None:
    web.api_snap_create(st, "A 만 올림", ["Foo/A.uasset"])
    log = web.api_log(st)
    top = log[0]
    assert top["uploaded"] is True and top["counts"]["modified"] == 1 and top["counts"]["added"] == 0
    assert log[1]["uploaded"] is True                    # #1 도 확정
    assert [x["role"] for x in log] == ["fix", "fix"]     # 작업 중 기록은 확정하며 사라진다
    assert web.api_snap(st, top["id"])["uploaded"] is True


def test_api_revert_preview_and_apply(st: storemod.Store, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storemod, "editor_running", lambda *a, **k: False)
    p = web.api_revert(st, ["Foo/A.uasset"])
    assert p["snapshot"]["id"] == 0 and p["diff"]["counts"]["modified"] == 1
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"   # 드라이런
    r = web.api_revert_apply(st, ["Foo/A.uasset"], False)
    assert r["ok"] is True and r["written"] == 1
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v1"
    assert (st.cfg.content / "Foo" / "B.uasset").exists()    # 고르지 않은 것은 그대로


def test_api_revert_blocked_409(st: storemod.Store, monkeypatch: pytest.MonkeyPatch) -> None:
    from jokate import bridge
    monkeypatch.setattr(storemod, "editor_running", lambda *a, **k: True)
    monkeypatch.setattr(bridge, "bridge_alive", lambda cfg, *a, **k: True)
    monkeypatch.setattr(bridge, "request", lambda cfg, op, pkgs, args=None, **k: {"ok": True, "dirty": list(pkgs)})
    with pytest.raises(storemod.RestoreBlocked) as ei:
        web.api_revert_apply(st, ["Foo/A.uasset"], False)
    code, body = web.error_response(ei.value)
    assert code == 409 and body["dirty"] == ["/Game/Foo/A"]
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"


# ---- 프로젝트별 에디터 판정 ----
def test_editor_running_matches_only_this_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storemod.sys, "platform", "win32")
    root = tmp_path / "MNYS"
    (root / "Content").mkdir(parents=True)
    cfgmod.init(root)
    (root / "MNYS.uproject").write_text("{}", encoding="utf-8")
    cfg = cfgmod.load(root)
    other = ['"C:/UE/UnrealEditor.exe" "D:/Other/Other.uproject"']
    mine = ['"C:/UE/UnrealEditor.exe" "%s"' % str(root / "MNYS.uproject").replace("/", "\\")]
    assert storemod.editor_running(cfg, query=lambda: other) is False
    assert storemod.editor_running(cfg, query=lambda: mine) is True
    # 조회 실패(None) → 기존 tasklist 방식으로 폴백 (보수적으로 참)
    monkeypatch.setattr(storemod, "_tasklist_has_editor", lambda: True)
    assert storemod.editor_running(cfg, query=lambda: None) is True
    assert storemod.editor_running() is True            # cfg 없으면 예전처럼 전체 판정


# ---- 에디터 클라이언트 ----
def test_client_revert_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []

    def fake_call(base, path, body=None, timeout=None):
        seen.append((path, body))
        return 200, {"ok": True}

    monkeypatch.setattr(jc, "_call", fake_call)
    assert jc.revert_assets("http://x", ["Foo/A.uasset"], discard_dirty=True)[0] == 200
    assert seen[-1] == ("/api/revert", {"assets": ["Foo/A.uasset"], "discard_dirty": True})
    jc.revert_preview("http://x", ["Foo/A.uasset", "Foo/B.uasset"])
    assert seen[-1][0].startswith("/api/revert?asset=") and seen[-1][1] is None
    assert seen[-1][0].count("asset=") == 2
    assert "마지막으로 올린 상태로 되돌립니다" in jc.format_preview(
        {"snapshot": {"id": 0, "message": "올린 상태(baseline)"}, "diff": {"counts": {}}})
