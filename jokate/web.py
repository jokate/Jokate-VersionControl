"""
타임라인 웹 UI (표준 라이브러리 http.server 만 사용)

  python -m jokate serve <project> [--port 8765]

JSON API
  GET  /api/log                      스냅샷 목록 + 변경 건수·클래스별 집계
  GET  /api/snap/<id>                show 와 동일한 diff (A/M/R/D + 클래스별)
  GET  /api/asset?rel=<rel>          애셋 버전 히스토리 (스냅샷별 sha·size·변경여부, 최신순)
  GET  /api/thumb?sha=<sha>          store 객체의 첫 썸네일 (image/jpeg|png, 없으면 404, 메모리 캐시)
  GET  /api/restore/<id>?asset=<rel> plan_restore 드라이런 (diff + broken + dependents). 적용 없음
  GET  /api/status                   HEAD 대비 아직 올리지 않은 변경 {diff}
  POST /api/snap  {message, only?:[rel]}   label 스냅샷 (only 있으면 부분 스냅샷)
  POST /api/restore/<id> {assets?:[rel], discard_dirty?:bool}  롤백 적용. 중단(dirty·브릿지) → 409 {ok:false,error,dirty}
  GET  /                             web_static/index.html

핸들러 로직은 api_* 순수 함수로 분리해 서버 없이 테스트한다.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import Config
from .store import Diff, RestoreBlocked, Snapshot, Store, TreeEntry, diff_trees, format_ts
from .uasset import read_package

STATIC = Path(__file__).parent / "web_static"


# ---- 직렬화 ----
def _entry(e: TreeEntry) -> dict:
    return {"rel": e.rel, "sha": e.sha, "size": e.size, "cls": e.cls or "?"}


def _snapshot(s: Snapshot) -> dict:
    return {"id": s.id, "parent": s.parent, "kind": s.kind, "message": s.message,
            "ts": s.ts, "time": format_ts(s.ts)}


def _diff(d: Diff) -> dict:
    return {
        "added": [_entry(e) for e in d.added],
        "modified": [{"old": _entry(o), "new": _entry(n)} for o, n in d.modified],
        "moved": [{"old": _entry(o), "new": _entry(n)} for o, n in d.moved],
        "deleted": [_entry(e) for e in d.deleted],
        "counts": {"added": len(d.added), "modified": len(d.modified),
                   "moved": len(d.moved), "deleted": len(d.deleted)},
        "by_class": {cls: dict(c) for cls, c in sorted(d.by_class().items())},
    }


# ---- API 로직 (서버 독립) ----
def api_log(store: Store) -> list[dict]:
    trees: dict[int | None, dict[str, TreeEntry]] = {None: {}}
    out = []
    for s in store.log():
        for sid in (s.id, s.parent):
            if sid not in trees:
                trees[sid] = store.tree(sid)
        d = diff_trees(trees[s.parent], trees[s.id])
        item = _snapshot(s)
        item["total"] = len(trees[s.id])
        item["counts"] = {"added": len(d.added), "modified": len(d.modified),
                          "moved": len(d.moved), "deleted": len(d.deleted)}
        item["by_class"] = {cls: dict(c) for cls, c in sorted(d.by_class().items())}
        out.append(item)
    return out


def api_snap(store: Store, sid: int) -> dict:
    s, d = store.show(sid)  # KeyError → 404
    return {"snapshot": _snapshot(s), "diff": _diff(d)}


def api_asset(store: Store, rel: str) -> dict:
    rel = rel.replace("\\", "/").strip("/")
    rows = store.db.execute(
        "SELECT s.id, s.kind, s.message, s.ts, t.sha, t.size, t.cls "
        "FROM snapshots s LEFT JOIN tree t ON t.snapshot_id = s.id AND t.rel = ? "
        "ORDER BY s.id ASC", (rel,)).fetchall()
    versions = []
    prev_sha: str | None = None
    for sid, kind, message, ts, sha, size, cls in rows:
        present = sha is not None
        if present:
            state = "added" if prev_sha is None else ("modified" if sha != prev_sha else "same")
        else:
            state = "deleted" if prev_sha is not None else "absent"
        if state != "absent":
            versions.append({"id": sid, "kind": kind, "message": message, "ts": ts, "time": format_ts(ts),
                             "sha": sha, "size": size, "cls": cls or "?", "state": state,
                             "changed": state in ("added", "modified", "deleted")})
        prev_sha = sha
    versions.reverse()
    return {"rel": rel, "versions": versions}


def api_restore(store: Store, sid: int, assets: list[str] | None = None) -> dict:
    plan = store.plan_restore(sid, assets or None)  # KeyError → 404
    return {"snapshot": _snapshot(plan.snapshot), "assets": assets or [],
            "diff": _diff(plan.diff),
            "broken": [{"rel": r, "dep": d} for r, d in plan.broken],
            "dependents": [{"rel": r, "dep": d} for r, d in plan.dependents]}


def api_status(store: Store) -> dict:
    """HEAD 대비 아직 올리지 않은 변경."""
    return {"diff": _diff(store.status())}


def api_snap_create(store: Store, message: str, only: list[str] | None = None) -> dict:
    """only 가 비면 전체(변경 없어도 생성). only 가 있으면 부분 스냅샷 — 선택한 것에 변경 없으면 snapshot=None."""
    only = [str(x) for x in (only or []) if str(x).strip()]
    snap, d, stored = store.snap(message, kind="label", force=not only, only=only or None)  # KeyError → 404
    return {"snapshot": _snapshot(snap) if snap else None, "diff": _diff(d), "stored": stored}


def api_restore_apply(store: Store, sid: int, assets: list[str] | None = None,
                      discard_dirty: bool = False) -> dict:
    """plan_restore → apply_restore. RestoreBlocked(→409)/KeyError(→404) 는 호출자가 처리."""
    plan = store.plan_restore(sid, assets or None)
    r = store.apply_restore(plan, discard_dirty=discard_dirty)
    return {"ok": True, "safety": _snapshot(r.safety), "result": _snapshot(r.result),
            "written": r.written, "deleted": r.deleted, "reloaded": r.reloaded}


def error_response(e: BaseException) -> tuple[int, dict]:
    """예외 → (HTTP 코드, JSON 본문). RestoreBlocked 는 409 + dirty 목록."""
    if isinstance(e, RestoreBlocked):
        return 409, {"ok": False, "error": str(e), "dirty": list(e.dirty)}
    if isinstance(e, (NotFound, KeyError)):
        return 404, {"error": str(e)}
    if isinstance(e, ValueError):   # json.JSONDecodeError 포함
        return 400, {"error": str(e)}
    return 500, {"error": f"{type(e).__name__}: {e}"}


_thumb_cache: dict[str, tuple[str, bytes] | None] = {}
_thumb_lock = threading.Lock()


def api_thumb(store: Store, sha: str) -> tuple[str, bytes] | None:
    """(content-type, bytes) 또는 None. 결과(없음 포함)는 메모리 캐시."""
    if not sha or any(c not in "0123456789abcdef" for c in sha.lower()):
        return None
    with _thumb_lock:
        if sha in _thumb_cache:
            return _thumb_cache[sha]
    result: tuple[str, bytes] | None = None
    p = store.object_path(sha)
    if p.exists():
        try:
            pkg = read_package(p, thumbnails=True)
            for t in pkg.thumbnails:
                if t.fmt in ("png", "jpeg"):
                    result = ("image/" + t.fmt, t.data)
                    break
        except Exception:
            result = None
    with _thumb_lock:
        _thumb_cache[sha] = result
    return result


# ---- HTTP ----
class NotFound(Exception):
    pass


def make_handler(cfg: Config):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # 조용히
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _run(self, fn):
            """Store 를 요청마다 열고(스레드 안전), 오류를 JSON 으로."""
            with lock:
                st = Store(cfg)
                try:
                    return fn(st)
                finally:
                    st.close()

        def do_GET(self) -> None:
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            path = u.path
            try:
                if path in ("/", "/index.html"):
                    self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/log":
                    self._json(self._run(api_log))
                elif path == "/api/status":
                    self._json(self._run(api_status))
                elif path.startswith("/api/snap/"):
                    sid = int(path.rsplit("/", 1)[1])
                    self._json(self._run(lambda st: api_snap(st, sid)))
                elif path == "/api/asset":
                    rel = q.get("rel", [""])[0]
                    if not rel:
                        raise ValueError("rel 필요")
                    self._json(self._run(lambda st: api_asset(st, rel)))
                elif path == "/api/thumb":
                    sha = q.get("sha", [""])[0]
                    r = self._run(lambda st: api_thumb(st, sha))
                    if r is None:
                        raise NotFound("썸네일 없음")
                    self._send(200, r[1], r[0])
                elif path.startswith("/api/restore/"):
                    sid = int(path.rsplit("/", 1)[1])
                    assets = q.get("asset", [])
                    self._json(self._run(lambda st: api_restore(st, sid, assets)))
                else:
                    raise NotFound(path)
            except Exception as e:  # noqa: BLE001
                code, payload = error_response(e)
                self._json(payload, code)

        def do_POST(self) -> None:
            u = urlsplit(self.path)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw.decode("utf-8") or "{}")
                if u.path == "/api/snap":
                    message = str(body.get("message", "")).strip()
                    if not message:
                        raise ValueError("message 필요")
                    only = body.get("only") or []
                    if not isinstance(only, list):
                        raise ValueError("only 는 rel 목록")
                    self._json(self._run(lambda st: api_snap_create(st, message, only)))
                elif u.path.startswith("/api/restore/"):
                    sid = int(u.path.rsplit("/", 1)[1])
                    assets = body.get("assets") or []
                    if not isinstance(assets, list):
                        raise ValueError("assets 는 rel 목록")
                    discard = bool(body.get("discard_dirty", False))
                    self._json(self._run(lambda st: api_restore_apply(st, sid, [str(x) for x in assets], discard)))
                else:
                    raise NotFound(u.path)
            except Exception as e:  # noqa: BLE001
                code, payload = error_response(e)
                self._json(payload, code)

    return Handler


def serve(cfg: Config, port: int = 8765, host: str = "127.0.0.1") -> int:
    httpd = ThreadingHTTPServer((host, port), make_handler(cfg))
    print(f"jokate 타임라인 → http://{host}:{port}/  (Ctrl+C 종료)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
