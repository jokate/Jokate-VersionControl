"""에디터 브릿지: 가짜 에디터(스레드)로 request() 왕복·타임아웃, apply_restore 의 dirty 중단/통과, bridge-install."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import __main__ as cli  # noqa: E402
from jokate import bridge  # noqa: E402
from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402


class FakeEditor:
    """1초 대신 0.05초마다 heartbeat 갱신 + request.json 처리. handler(op, packages, args) -> dict."""

    def __init__(self, cfg, handler=None, heartbeat=True):
        self.d = bridge.bridge_dir(cfg)
        self.d.mkdir(parents=True, exist_ok=True)
        self.handler = handler
        self.heartbeat = heartbeat
        self.calls: list[tuple[str, list[str], dict]] = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._t.start()
        time.sleep(0.1)
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(2)

    def _run(self):
        while not self._stop.is_set():
            if self.heartbeat:
                bridge._write_json(self.d / "heartbeat.json", {"ts": time.time()})
            req = self.d / "request.json"
            if self.handler and req.exists():
                try:
                    r = json.loads(req.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    r = None
                if r:
                    req.unlink()
                    self.calls.append((r["op"], r["packages"], r["args"]))
                    resp = self.handler(r["op"], r["packages"], r["args"])
                    resp["id"] = r["id"]
                    bridge._write_json(self.d / f"response-{r['id']}.json", resp)
            time.sleep(0.05)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "Proj"
    (root / "Content" / "Foo").mkdir(parents=True)
    cfgmod.init(root)
    (root / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA-v1")
    return root


def test_bridge_alive(project: Path) -> None:
    cfg = cfgmod.load(project)
    assert bridge.heartbeat_age(cfg) is None
    assert not bridge.bridge_alive(cfg)
    d = bridge.bridge_dir(cfg); d.mkdir(parents=True)
    bridge._write_json(d / "heartbeat.json", {"ts": time.time() - 10})
    assert not bridge.bridge_alive(cfg)
    bridge._write_json(d / "heartbeat.json", {"ts": time.time()})
    assert bridge.bridge_alive(cfg)


def test_request_roundtrip(project: Path) -> None:
    cfg = cfgmod.load(project)
    with FakeEditor(cfg, lambda op, p, a: {"ok": True, "echo": [op, p, a]}) as ed:
        r = bridge.request(cfg, "dirty", ["/Game/Foo/A"], {"x": 1}, timeout=3)
    assert r["ok"] and r["echo"] == ["dirty", ["/Game/Foo/A"], {"x": 1}]
    assert ed.calls == [("dirty", ["/Game/Foo/A"], {"x": 1})]
    assert not list(bridge.bridge_dir(cfg).glob("response-*"))


def test_request_timeout(project: Path) -> None:
    cfg = cfgmod.load(project)
    with FakeEditor(cfg, handler=None):
        with pytest.raises(TimeoutError):
            bridge.request(cfg, "dirty", ["/Game/Foo/A"], timeout=0.5)
    assert not (bridge.bridge_dir(cfg) / "request.json").exists()


def _two_snapshots(project: Path) -> storemod.Store:
    st = storemod.Store(cfgmod.load(project))
    st.snap("first", kind="label")
    (project / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")
    st.snap("second", kind="label")
    return st


def test_apply_restore_no_bridge(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storemod, "editor_running", lambda *a, **k: True)
    st = _two_snapshots(project)
    with pytest.raises(RuntimeError, match="브릿지"):
        st.apply_restore(st.plan_restore(1))
    assert (project / "Content" / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"


def test_apply_restore_dirty_blocks(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storemod, "editor_running", lambda *a, **k: True)
    st = _two_snapshots(project)

    def handler(op, pkgs, args):
        if op == "dirty":
            return {"ok": True, "dirty": pkgs}
        return {"ok": True, "reloaded": len(pkgs)}

    with FakeEditor(st.cfg, handler) as ed:
        with pytest.raises(RuntimeError, match="/Game/Foo/A"):
            st.apply_restore(st.plan_restore(1))
        assert [c[0] for c in ed.calls] == ["dirty"]
        assert (project / "Content" / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"
        assert st.head().id == 2   # 안전 스냅샷도 안 만듦

        r = st.apply_restore(st.plan_restore(1), discard_dirty=True)
    assert (project / "Content" / "Foo" / "A.uasset").read_bytes() == b"AAAA-v1"
    assert r.written == 1 and r.reloaded == 1
    assert [c[0] for c in ed.calls] == ["dirty", "dirty", "release", "reload"]
    assert ed.calls[-1] == ("reload", ["/Game/Foo/A"], {"discard_dirty": True})


def test_apply_restore_release_order(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """dirty → release → (쓰기) → reload 순서. release 는 쓰기 전에 와야 한다."""
    monkeypatch.setattr(storemod, "editor_running", lambda *a, **k: True)
    st = _two_snapshots(project)
    seen: list[str] = []
    asset = project / "Content" / "Foo" / "A.uasset"

    def handler(op, pkgs, args):
        seen.append(f"{op}:{asset.read_bytes().decode()}")
        if op == "dirty":
            return {"ok": True, "dirty": []}
        if op == "release":
            return {"ok": True, "released": list(pkgs), "not_loaded": [], "failed": {}}
        return {"ok": True, "reloaded": len(pkgs)}

    with FakeEditor(st.cfg, handler) as ed:
        r = st.apply_restore(st.plan_restore(1))
    assert [c[0] for c in ed.calls] == ["dirty", "release", "reload"]
    assert seen == ["dirty:AAAA-v2", "release:AAAA-v2", "reload:AAAA-v1"]
    assert ed.calls[1] == ("release", ["/Game/Foo/A"], {})
    assert r.written == 1 and asset.read_bytes() == b"AAAA-v1"


def test_apply_restore_release_failure_ignored(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """release 가 실패를 돌려줘도 사전 잠금 검사가 통과하면 그대로 진행한다."""
    monkeypatch.setattr(storemod, "editor_running", lambda *a, **k: True)
    st = _two_snapshots(project)

    def handler(op, pkgs, args):
        if op == "dirty":
            return {"ok": True, "dirty": []}
        if op == "release":
            return {"ok": False, "released": [], "not_loaded": [], "failed": {pkgs[0]: "boom"}}
        return {"ok": True, "reloaded": len(pkgs)}

    with FakeEditor(st.cfg, handler):
        r = st.apply_restore(st.plan_restore(1))
    assert r.written == 1
    assert (project / "Content" / "Foo" / "A.uasset").read_bytes() == b"AAAA-v1"


def test_apply_restore_reload_fail(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storemod, "editor_running", lambda *a, **k: True)
    st = _two_snapshots(project)
    handler = lambda op, p, a: {"ok": True, "dirty": []} if op == "dirty" else {"ok": False, "error": "boom"}  # noqa: E731
    with FakeEditor(st.cfg, handler):
        with pytest.raises(RuntimeError, match="boom"):
            st.apply_restore(st.plan_restore(1))
    assert (project / "Content" / "Foo" / "A.uasset").read_bytes() == b"AAAA-v1"   # 파일은 이미 적용됨


def test_cli_bridge_install_and_status(project: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["bridge-install", str(project)]) == 0
    pydir = project / "Content" / "Python"
    assert (pydir / "jokate_bridge.py").read_bytes() == bridge._SRC.read_bytes()
    assert (pydir / "init_unreal.py").read_text(encoding="utf-8").strip() == "import jokate_bridge"
    (pydir / "init_unreal.py").write_text("import other\n", encoding="utf-8")
    assert cli.main(["bridge-install", str(project)]) == 0
    assert (pydir / "init_unreal.py").read_text(encoding="utf-8") == "import other\nimport jokate_bridge\n"
    assert cli.main(["bridge-install", str(project)]) == 0
    assert (pydir / "init_unreal.py").read_text(encoding="utf-8").count("jokate_bridge") == 1
    assert cli.main(["bridge-status", str(project)]) == 1
    assert "브릿지 없음" in capsys.readouterr().out
    # 스레드 heartbeat 대신 정적 파일(넉넉한 미래 ts → 나이 0) 로 결정적으로 판정
    cfg = cfgmod.load(project)
    d = bridge.bridge_dir(cfg); d.mkdir(parents=True, exist_ok=True)
    bridge._write_json(d / "heartbeat.json", {"ts": time.time() + 3600})
    assert cli.main(["bridge-status", str(project)]) == 0
    assert "살아 있음" in capsys.readouterr().out
    bridge._write_json(d / "heartbeat.json", {"ts": time.time() - 3600})
    assert cli.main(["bridge-status", str(project)]) == 1
    assert "끊김" in capsys.readouterr().out


def test_heartbeat_age_now_injection(project: Path) -> None:
    cfg = cfgmod.load(project)
    d = bridge.bridge_dir(cfg); d.mkdir(parents=True)
    bridge._write_json(d / "heartbeat.json", {"ts": 1000.0})
    assert bridge.heartbeat_age(cfg, now=1002.5) == 2.5
    assert bridge.bridge_alive(cfg, now=1002.5) and not bridge.bridge_alive(cfg, now=1004.0)
