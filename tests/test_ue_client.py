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


def _e(rel, noise=False):
    return {"rel": rel, "sha": "a", "size": 1, "cls": "Blueprint", "noise": noise}


PREVIEW = {
    "snapshot": {"id": 7, "message": "몬스터 밸런스"},
    "assets": [],
    "diff": {
        "modified": [{"old": _e("Blueprint/BP_Monster.uasset"), "new": _e("Blueprint/BP_Monster.uasset")},
                     {"old": _e("Blueprint/BP_Noise.uasset"), "new": _e("Blueprint/BP_Noise.uasset", True)}],
        "added": [_e("Blueprint/BP_Back.uasset")],
        "moved": [{"old": _e("A/Old.uasset"), "new": _e("A/New.uasset")}],
        "deleted": [_e("Blueprint/BP_Gone.uasset")],
        "counts": {"added": 1, "modified": 1, "resave": 1, "moved": 1, "deleted": 1},
    },
    "broken": [{"rel": "Blueprint/BP_Gone.uasset", "dep": "/Game/Blueprint/BP_Child"}],
    "dependents": [{"rel": "A/Old.uasset", "dep": "/Game/Maps/Lobby"}],
}


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
        elif self.path.startswith("/api/restore/"):
            CALLS.append((self.path, None))
            self._json(200, PREVIEW)
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
    code, j = jc.snap_only(base, "에디터에서 확정: A 1개", ["Foo/A.uasset"])
    assert code == 200 and j["snapshot"]["id"] == 8
    assert CALLS[-1] == ("/api/snap", {"message": "에디터에서 확정: A 1개", "only": ["Foo/A.uasset"]})


def test_restore_409_then_discard(base):
    code, j = jc.restore_assets(base, 7, ["Foo/A.uasset"])
    assert code == 409 and j["ok"] is False and j["dirty"] == ["/Game/Foo/A"]
    code, j = jc.restore_assets(base, 7, ["Foo/A.uasset"], discard_dirty=True)
    assert code == 200 and j["result"]["id"] == 9
    assert CALLS[-1][0] == "/api/restore/7" and CALLS[-1][1]["discard_dirty"] is True


def test_restore_preview_roundtrip(base):
    CALLS.clear()
    code, j = jc.restore_preview(base, 7, ["Blueprint/BP_Monster.uasset", "A/Old.uasset"])
    assert code == 200 and j["snapshot"]["id"] == 7
    path = CALLS[-1][0]
    assert path.startswith("/api/restore/7?")
    assert path.count("asset=") == 2 and "Blueprint%2FBP_Monster.uasset" in path


def test_preview_change_count():
    assert jc.preview_change_count(PREVIEW) == 5  # 수정2(리세이브 포함) + 부활1 + 이동1 + 삭제1
    assert jc.preview_change_count({"diff": {"counts": {}}}) == 0


def test_format_preview():
    text = jc.format_preview(PREVIEW, 7)
    lines = text.splitlines()
    assert lines[0] == "#7 몬스터 밸런스 버전으로 되돌립니다"
    assert lines[1] == "수정 1 · 부활 1 · 이동 1 · 삭제 1 · 리세이브만 1"
    assert "M Blueprint/BP_Monster.uasset" in lines
    assert "M Blueprint/BP_Noise.uasset (리세이브만)" in lines
    assert "A Blueprint/BP_Back.uasset" in lines
    assert "R A/Old.uasset → A/New.uasset" in lines
    assert "D Blueprint/BP_Gone.uasset" in lines
    assert "참조 경고 2건" in lines
    assert lines[-2] == ("되돌릴 애셋의 현재 상태만 실행 취소 지점으로 남습니다. "
                         "다른 애셋의 확정 안 된 변경은 그대로 유지됩니다.")
    # 과거 버전으로 되돌린 결과는 '확정 안 된 변경' 이라는 안내
    assert lines[-1] == "되돌린 결과는 '확정 안 된 변경' 으로 나타납니다 — 되돌린 뒤 확정을 눌러야 새 버전이 됩니다."


def test_format_preview_row_limit():
    text = jc.format_preview(PREVIEW, 7, max_rows=2)
    assert "… 외 3개" in text.splitlines()
    assert "D Blueprint/BP_Gone.uasset" not in text


def test_format_blocked_and_result():
    body = {"ok": False, "error": "dirty 패키지 있음", "dirty": ["/Game/Foo/A%d" % i for i in range(12)]}
    b = jc.format_blocked(body)
    assert "dirty 패키지 있음" in b and "저장 안 된 패키지 12개:" in b
    assert b.count("/Game/Foo/A") == 10 and "… 외 2개" in b
    r = jc.format_result({"ok": True, "result": {"id": 9}, "safety": {"id": 8}, "written": 3, "deleted": 1})
    assert r == "되돌리기 완료 #9 · 실행 취소 지점 #8 · 복사 3 · 삭제 1"
    # 변경 버리기 응답에는 undo 가 온다 — 그쪽을 실행 취소 지점으로 쓴다
    r2 = jc.format_result({"ok": True, "result": {"id": 9}, "safety": {"id": 8}, "undo": {"id": 5},
                           "written": 3, "deleted": 1, "safety_created": False})
    assert r2 == "되돌리기 완료 #9 · 실행 취소 지점 #5 · 복사 3 · 삭제 1"


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
