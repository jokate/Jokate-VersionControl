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
    monkeypatch.setattr(storemod, "editor_running", lambda: True)
    st = _two_snapshots(project)
    with pytest.raises(RuntimeError, match="브릿지"):
        st.apply_restore(st.plan_restore(1))
    assert (project / "Content" / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"


def test_apply_restore_dirty_blocks(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storemod, "editor_running", lambda: True)
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
    assert [c[0] for c in ed.calls] == ["dirty", "dirty", "reload"]
    assert ed.calls[-1] == ("reload", ["/Game/Foo/A"], {"discard_dirty": True})


def test_apply_restore_reload_fail(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storemod, "editor_running", lambda: True)
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
    with FakeEditor(cfgmod.load(project)):
        assert cli.main(["bridge-status", str(project)]) == 0
    assert "살아 있음" in capsys.readouterr().out
