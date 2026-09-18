"""jokate/ue/jokate_client.py: 가짜 API(http.server 스레드) 왕복, 409 처리, package_to_rel, port 읽기."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jokate" / "ue"))

import jokate_client as jc  # noqa: E402

CALLS: list = []


class FakeApi(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D102
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/api/log":
            self._json(200, [{"id": 7, "message": "x"}, {"id": 3}])
        elif self.path == "/api/status":
            self._json(200, {"diff": {"counts": {"modified": 1}}})
        else:
            self._json(404, {"ok": False, "error": "nf"})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        CALLS.append((self.path, body))
        if self.path == "/api/snap":
            self._json(200, {"snapshot": {"id": 8}, "stored": len(body.get("only", []))})
        elif self.path.startswith("/api/restore/"):
            if body.get("discard_dirty"):
                self._json(200, {"ok": True, "result": {"id": 9}, "safety": {"id": 8}})
            else:
                self._json(409, {"ok": False, "error": "dirty", "dirty": ["/Game/Foo/A"]})
        else:
            self._json(404, {"ok": False})


@pytest.fixture(scope="module")
def base():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_head_and_status(base):
    assert jc.head_id(base) == 7
    assert jc.status(base)["diff"]["counts"]["modified"] == 1


def test_snap_only_roundtrip(base):
    CALLS.clear()
    code, j = jc.snap_only(base, "에디터에서 올림: A 1개", ["Foo/A.uasset"])
    assert code == 200 and j["snapshot"]["id"] == 8
    assert CALLS[-1] == ("/api/snap", {"message": "에디터에서 올림: A 1개", "only": ["Foo/A.uasset"]})


def test_restore_409_then_discard(base):
    code, j = jc.restore_assets(base, 7, ["Foo/A.uasset"])
    assert code == 409 and j["ok"] is False and j["dirty"] == ["/Game/Foo/A"]
    code, j = jc.restore_assets(base, 7, ["Foo/A.uasset"], discard_dirty=True)
    assert code == 200 and j["result"]["id"] == 9
    assert CALLS[-1][0] == "/api/restore/7" and CALLS[-1][1]["discard_dirty"] is True


def test_connection_refused():
    with pytest.raises(jc.ConnectionError):
        jc.head_id("http://127.0.0.1:1")


def test_package_to_rel(tmp_path: Path):
    content = tmp_path / "Content"
    (content / "Maps").mkdir(parents=True)
    (content / "Maps" / "Lobby.umap").write_bytes(b"x")
    assert jc.package_to_rel("/Game/Blueprint/BP_Monster", content) == "Blueprint/BP_Monster.uasset"
    assert jc.package_to_rel("/Game/Maps/Lobby", content) == "Maps/Lobby.umap"
    assert jc.package_to_rel("/Engine/Foo", content) is None
    assert jc.package_to_rel("/Game/", content) is None


def test_port_and_urls(tmp_path: Path):
    assert jc.read_port(tmp_path) == 8765
    (tmp_path / ".jokate").mkdir()
    (tmp_path / ".jokate" / "config.toml").write_text("[project]\ncontent = \"Content\"\n\n[web]\nport = 9001  # x\n", encoding="utf-8")
    assert jc.read_port(tmp_path) == 9001
    assert jc.base_url(tmp_path) == "http://127.0.0.1:9001"
    assert jc.web_url("http://h:1", "A/B.uasset") == "http://h:1/?asset=A%2FB.uasset"
    assert jc.web_url("http://h:1", view="status") == "http://h:1/?view=status"
