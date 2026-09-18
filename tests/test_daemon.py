"""데몬 컨트롤·상태 파일·데몬 API 단위 테스트 (실제 소켓은 포트 0 으로 잠깐만)."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import config as cfgmod  # noqa: E402
from jokate import daemon as daemonmod  # noqa: E402
from jokate import web  # noqa: E402


@pytest.fixture
def cfg(tmp_path: Path):
    root = tmp_path / "Proj"
    (root / "Content" / "Foo").mkdir(parents=True)
    (root / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA")
    cfgmod.init(root)
    return cfgmod.load(root)


def test_control_transitions(cfg) -> None:
    c = daemonmod.DaemonControl(cfg, 1234)
    assert c.paused is False and c.stopping is False
    assert c.status()["running"] is True and c.status()["port"] == 1234
    c.pause()
    assert c.paused is True and c.status()["paused"] is True
    c.pause()  # 멱등
    assert c.paused is True
    c.resume()
    assert c.paused is False
    c.stop()
    assert c.stopping is True and c.wait_stop(0) is True
    assert "종료 요청" in c.last_line
    assert "종료 요청" in daemonmod.log_path(cfg).read_text(encoding="utf-8")


def test_api_daemon_get_post(cfg) -> None:
    assert web.api_daemon_get(None) == {"running": False, "paused": False, "pid": None,
                                        "port": None, "started": None, "last_line": None}
    with pytest.raises(web.DaemonUnavailable):
        web.api_daemon_post(None, "pause")
    assert web.error_response(web.DaemonUnavailable("x"))[0] == 409

    c = daemonmod.DaemonControl(cfg, 1)
    assert web.api_daemon_get(c)["running"] is True
    assert web.api_daemon_post(c, "pause")["paused"] is True
    assert web.api_daemon_post(c, "resume")["paused"] is False
    assert web.api_daemon_post(c, "stop")["stopping"] is True
    with pytest.raises(ValueError):
        web.api_daemon_post(c, "nope")


def test_state_file(cfg) -> None:
    assert daemonmod.read_state(cfg) is None
    s = daemonmod.write_state(cfg, 8765)
    assert daemonmod.state_path(cfg).exists()
    assert s["port"] == 8765 and s["pid"] > 0 and s["started"] and s["tool_dir"]
    assert daemonmod.read_state(cfg) == s
    daemonmod.clear_state(cfg)
    assert daemonmod.read_state(cfg) is None
    daemonmod.clear_state(cfg)  # 없어도 조용히


def test_running_state_dead_pid(cfg) -> None:
    assert daemonmod.pid_alive(None) is False
    assert daemonmod.pid_alive(0) is False
    assert daemonmod.pid_alive(999999999) is False
    daemonmod.state_path(cfg).write_text(json.dumps({"pid": 999999999, "port": 65500}), encoding="utf-8")
    assert daemonmod.running_state(cfg) is None
    daemonmod.state_path(cfg).write_text("{ 깨짐", encoding="utf-8")
    assert daemonmod.read_state(cfg) is None and daemonmod.running_state(cfg) is None


def test_daemon_http(cfg) -> None:
    """포트 0 으로 잠깐 띄워 /api/daemon GET·POST 확인."""
    c = daemonmod.DaemonControl(cfg, 0)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(cfg, c))
    c.port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{c.port}"
    try:
        with urllib.request.urlopen(base + "/api/daemon", timeout=5) as r:
            got = json.loads(r.read().decode("utf-8"))
        assert got["running"] is True and got["paused"] is False and got["pid"] == c.pid

        req = urllib.request.Request(base + "/api/daemon", data=json.dumps({"action": "pause"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read().decode("utf-8"))["paused"] is True
        assert c.paused is True
        assert daemonmod.ping(c.port) is True
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=3)
    assert daemonmod.ping(c.port) is False


def test_serve_mode_has_no_control(cfg) -> None:
    """control 없이 make_handler 를 만들면 POST /api/daemon 은 409."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(cfg))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/daemon", timeout=5) as r:
            assert json.loads(r.read().decode("utf-8"))["running"] is False
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/daemon",
                                     data=json.dumps({"action": "stop"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=5)
        assert ei.value.code == 409
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=3)


# ---- 재시작 (실제 프로세스는 절대 띄우지 않는다: 가짜 launcher) ----
def test_control_restart_spawns_and_stops(cfg) -> None:
    c = daemonmod.DaemonControl(cfg, 4321)
    seen = []

    def fake_launcher(conf, port=None):
        seen.append((conf.root, port))
        return 9191

    s = c.restart(launcher=fake_launcher)
    assert seen == [(cfg.root, 4321)]
    assert s["restarted_pid"] == 9191 and c.stopping is True
    assert s["stopping"] is True          # 새 프로세스를 띄운 뒤 자신은 종료 요청 상태


def test_relaunch_command_uses_module(cfg) -> None:
    cmd = daemonmod.relaunch_command(cfg)
    assert cmd[1:] == ["-m", "jokate", "daemon", str(cfg.root)] and cmd[0]


def test_wait_port_free_returns_when_no_daemon(cfg) -> None:
    assert daemonmod.wait_port_free(cfg, timeout=1.0, poll=0.05) is True
