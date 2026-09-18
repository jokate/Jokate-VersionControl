"""
타임라인 웹 UI (표준 라이브러리 http.server 만 사용)

  python -m jokate serve <project> [--port 8765]

JSON API
  GET  /api/log                      스냅샷 목록 + 변경 건수·클래스별 집계
  GET  /api/snap/<id>                show 와 동일한 diff (A/M/R/D + 클래스별)
  GET  /api/asset?rel=<rel>          애셋 버전 히스토리 (스냅샷별 sha·size·변경여부, 최신순)
  GET  /api/thumb?sha=<sha>          store 객체의 첫 썸네일 (image/jpeg|png, 없으면 404, 메모리 캐시)
  GET  /api/thumb?rel=<rel>          작업 트리(Content/<rel>) 현재 파일의 첫 썸네일 (경로 탈출 404)
  GET  /api/search?q=<q>             애셋·클래스·메시지 부분일치(대소문자 무시) 스냅샷 검색
  GET  /api/metadiff?a=<sha>&b=<sha> 의미 diff (DataTable 수치 변경) {available, missing, kind, diff}
  GET  /api/restore/<id>?asset=<rel> plan_restore 드라이런 (diff + broken + dependents). 적용 없음
  GET  /api/status                   HEAD 대비 아직 올리지 않은 변경 {diff}
  GET  /api/daemon                   {running, paused, pid, port, started, last_line}
  POST /api/daemon {action}          pause|resume|stop|restart (데몬 모드가 아니면 409)
  GET  /api/info                     요약 + build/build_disk/stale (낡은 서버 감지)
  POST /api/snap  {message, only?:[rel]}   label 스냅샷 (only 있으면 부분 스냅샷)
  POST /api/restore/<id> {assets?:[rel], discard_dirty?:bool}  롤백 적용. 중단(dirty·브릿지) → 409 {ok:false,error,dirty}
  POST /api/squash {ids:[id], message, include_labels}
                                          연속 스냅샷 묶기 (사슬 아니면 400, 라벨 포함이면 409+labels)
  POST /api/uediff {rel, a, b?}            두 버전을 UE diff 창으로 (b 없으면 현재 파일).
                                          에디터 못 찾으면 409 {ok:false,error}
  POST /api/prune  {dry_run:bool}          오래된 auto 스냅샷 정리 + GC → {ids, objects, bytes}
  GET  /                             web_static/index.html

핸들러 로직은 api_* 순수 함수로 분리해 서버 없이 테스트한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import meta as metamod
from . import uediff as uediffmod
from .config import Config
from .store import (Diff, RestoreBlocked, Snapshot, SquashHasLabels, Store, TreeEntry,
                    diff_trees, format_ts)
from .uasset import read_package

_HEX = re.compile(r"[0-9a-fA-F]+")

STATIC = Path(__file__).parent / "web_static"


# ---- 빌드 식별자 (낡은 서버 감지) ----
def compute_build_id(base: Path | None = None) -> str:
    """jokate/*.py 와 web_static/* 의 (상대경로, mtime, size) 해시 → 짧은 문자열."""
    base = Path(base) if base is not None else Path(__file__).resolve().parent
    parts: list[str] = []
    files = sorted(base.glob("*.py")) + sorted((base / "web_static").glob("*"))
    for p in files:
        try:
            stt = p.stat()
        except OSError:
            continue
        if not p.is_file():
            continue
        parts.append(f"{p.relative_to(base).as_posix()}:{stt.st_mtime_ns}:{stt.st_size}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]


BUILD_ID = compute_build_id()          # 서버가 시작될 때의 코드 상태
BUILD_CACHE_TTL = 5.0
_build_cache = {"ts": 0.0, "id": BUILD_ID}


def current_build(now: float | None = None) -> str:
    """디스크의 현재 코드 상태 (5초 캐시)."""
    t = time.time() if now is None else now
    if t - _build_cache["ts"] >= BUILD_CACHE_TTL:
        _build_cache["id"] = compute_build_id()
        _build_cache["ts"] = t
    return str(_build_cache["id"])


# ---- 직렬화 ----
def _entry(e: TreeEntry) -> dict:
    return {"rel": e.rel, "sha": e.sha, "size": e.size, "cls": e.cls or "?", "noise": bool(e.noise)}


def _counts(d: Diff) -> dict:
    """modified 는 실제 변경 수, resave 는 리세이브만(noise) 수."""
    return {"added": len(d.added), "modified": len(d.real_modified), "resave": len(d.resave),
            "moved": len(d.moved), "deleted": len(d.deleted)}


def _snapshot(s: Snapshot) -> dict:
    return {"id": s.id, "parent": s.parent, "kind": s.kind, "message": s.message,
            "ts": s.ts, "time": format_ts(s.ts)}


def _diff(d: Diff) -> dict:
    return {
        "added": [_entry(e) for e in d.added],
        "modified": [{"old": _entry(o), "new": _entry(n)} for o, n in d.modified],
        "moved": [{"old": _entry(o), "new": _entry(n)} for o, n in d.moved],
        "deleted": [_entry(e) for e in d.deleted],
        "counts": _counts(d),
        "by_class": {cls: dict(c) for cls, c in sorted(d.by_class().items())},
        "all_noise": d.all_noise,
    }


# ---- API 로직 (서버 독립) ----
def api_info(store: Store) -> dict:
    """상단 요약: 프로젝트명, 스냅샷 수, HEAD 추적 애셋 수, 객체 수·용량, 마지막 스냅샷."""
    head = store.head()
    n_snaps = store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    n_assets = store.db.execute("SELECT COUNT(*) FROM tree WHERE snapshot_id=?", (head.id,)).fetchone()[0] if head else 0
    objects = 0
    size = 0
    if store.objects.exists():
        for p in store.objects.rglob("*"):
            if p.is_file() and not p.suffix:
                objects += 1
                size += p.stat().st_size
    disk = current_build()
    return {"project": store.cfg.root.name, "snapshots": n_snaps, "assets": n_assets,
            "objects": objects, "store_bytes": size, "last": _snapshot(head) if head else None,
            "build": BUILD_ID, "build_disk": disk, "stale": disk != BUILD_ID}


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
        item["counts"] = _counts(d)
        item["by_class"] = {cls: dict(c) for cls, c in sorted(d.by_class().items())}
        item["all_noise"] = d.all_noise
        out.append(item)
    return out


def api_search(store: Store, q: str, limit: int = 200) -> dict:
    """애셋 rel·클래스·스냅샷 메시지 부분일치(대소문자 무시)로 스냅샷 검색(최신순).

    각 스냅샷은 '직전 대비 diff 에 포함된' 애셋만 본다(변경 없는 애셋은 일치하지 않음).
    트리는 api_log 처럼 한 번만 읽어 캐시를 공유한다.
    """
    needle = (q or "").strip().lower()
    if not needle:
        return {"q": "", "snapshots": []}
    trees: dict[int | None, dict[str, TreeEntry]] = {None: {}}
    out: list[dict] = []
    for s in store.log():
        for sid in (s.id, s.parent):
            if sid not in trees:
                trees[sid] = store.tree(sid)
        d = diff_trees(trees[s.parent], trees[s.id])
        entries: list[TreeEntry] = [*d.added, *d.deleted]
        for o, n in (*d.modified, *d.moved):
            entries += [n, o]
        matched: list[str] = []
        seen: set[str] = set()
        by_cls = False
        for e in entries:
            hit_rel = needle in e.rel.lower()
            hit_cls = needle in (e.cls or "?").lower()   # 미지 클래스는 UI 와 같게 '?'
            if (hit_rel or hit_cls) and e.rel not in seen:
                seen.add(e.rel)
                matched.append(e.rel)
                by_cls = by_cls or (hit_cls and not hit_rel)
        hit_msg = needle in (s.message or "").lower()
        if not matched and not hit_msg:
            continue
        by = "message" if not matched else ("class" if by_cls and not any(
            needle in r.lower() for r in matched) else "asset")
        out.append({"id": s.id, "matched": matched, "by": by})
        if len(out) >= limit:
            break
    return {"q": needle, "snapshots": out}


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
                             "changed": state in ("added", "modified", "deleted"),
                             "has_meta": bool(sha) and metamod.has_meta(store, sha)})
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
    if snap is not None:
        metamod.capture_for_snapshot(store, d)
    return {"snapshot": _snapshot(snap) if snap else None, "diff": _diff(d), "stored": stored}


def api_metadiff(store: Store, a_sha: str, b_sha: str) -> dict:
    """두 버전의 사이드카(의미 메타)를 비교. a 가 비면 '새로 추가'로 보고 b 전체를 rows_added 로."""
    a_sha = (a_sha or "").strip()
    b_sha = (b_sha or "").strip()
    for s in (a_sha, b_sha):
        if s and not _HEX.fullmatch(s):
            raise ValueError("sha 는 16진수만")
    if not b_sha:
        raise ValueError("b 필요")
    b = metamod.load_meta(store, b_sha)
    a = metamod.load_meta(store, a_sha) if a_sha else None
    missing = []
    if a_sha and a is None:
        missing.append("a")
    if b is None:
        missing.append("b")
    if missing:
        return {"available": False, "missing": missing, "kind": "", "diff": None}
    return {"available": True, "missing": [], "kind": b.get("kind", ""),
            "row_struct": b.get("row_struct", ""), "diff": metamod.diff_tables(a, b)}


def api_restore_apply(store: Store, sid: int, assets: list[str] | None = None,
                      discard_dirty: bool = False) -> dict:
    """plan_restore → apply_restore. RestoreBlocked(→409)/KeyError(→404) 는 호출자가 처리."""
    plan = store.plan_restore(sid, assets or None)
    r = store.apply_restore(plan, discard_dirty=discard_dirty)
    return {"ok": True, "safety": _snapshot(r.safety), "result": _snapshot(r.result),
            "written": r.written, "deleted": r.deleted, "reloaded": r.reloaded,
            "safety_created": r.safety_created}


def api_squash(store: Store, ids: list[int], message: str, include_labels: bool = False) -> dict:
    """연속된 스냅샷들을 하나로 묶고 라벨을 붙인다. 사슬이 아니면 ValueError(→400).

    사라질 쪽에 이름 붙인 스냅샷이 있으면 SquashHasLabels(→409). include_labels 면 강행.
    """
    try:
        want = [int(x) for x in (ids or [])]
    except (TypeError, ValueError) as e:
        raise ValueError("ids 는 스냅샷 id 목록") from e
    snap, removed = store.squash(want, str(message or "").strip(), include_labels=include_labels)
    return {"snapshot": _snapshot(snap), "removed": removed}


def api_prune(store: Store, dry_run: bool = True) -> dict:
    """오래된 auto 스냅샷 정리(+객체 GC). dry_run 이면 지울 것만 계산. → {ids, objects, bytes}"""
    cfg = store.cfg
    ids = store.prune(auto_days=cfg.auto_days, keep_last_auto=cfg.keep_last_auto, dry_run=True)
    if dry_run:
        objects, size = store.gc(dry_run=True, exclude_snapshots=ids)
        return {"ids": ids, "objects": objects, "bytes": size}
    if ids:
        store.delete_snapshots(ids)
    objects, size = store.gc()
    return {"ids": ids, "objects": objects, "bytes": size}


def api_uediff(store: Store, rel: str, a_sha: str, b_sha: str | None = None, launcher=None) -> dict:
    """애셋의 두 버전을 UE diff 창으로 연다. b 가 없으면 오른쪽은 작업 트리 현재 파일.

    EditorNotFound(→409) / KeyError(객체 없음 →404) / ValueError(→400) 는 호출자가 처리.
    """
    r = uediffmod.open_diff(store, rel, a_sha, b_sha or None, launcher=launcher)
    return {"ok": True, "mode": r.get("mode", "process"), "strategy": r.get("strategy"),
            "note": r.get("note", ""), "pid": r["pid"], "left": r["left"], "right": r["right"]}


class DaemonUnavailable(Exception):
    """serve 단독 모드라 데몬 조작이 불가."""


def api_daemon_get(control=None) -> dict:
    """데몬 상태. control 이 없으면(serve 단독) running=False."""
    if control is None:
        return {"running": False, "paused": False, "pid": None, "port": None,
                "started": None, "last_line": None}
    return control.status()


def api_daemon_post(control, action: str) -> dict:
    """action: pause | resume | stop | restart. control 이 없으면 DaemonUnavailable(409)."""
    if control is None:
        raise DaemonUnavailable("데몬 모드가 아닙니다 (serve 단독)")
    if action == "pause":
        return control.pause()
    if action == "resume":
        return control.resume()
    if action == "stop":
        return control.stop()
    if action == "restart":
        return control.restart()
    raise ValueError(f"알 수 없는 action: {action}")


def error_response(e: BaseException) -> tuple[int, dict]:
    """예외 → (HTTP 코드, JSON 본문). RestoreBlocked·DaemonUnavailable·FileNotFoundError 는 409."""
    if isinstance(e, uediffmod.EditorNotFound):
        return 409, {"ok": False, "error": str(e)}
    if isinstance(e, DaemonUnavailable):
        return 409, {"ok": False, "error": str(e)}
    if isinstance(e, RestoreBlocked):
        return 409, {"ok": False, "error": str(e), "dirty": list(e.dirty), "locked": list(e.locked)}
    if isinstance(e, SquashHasLabels):
        return 409, {"ok": False, "error": str(e),
                     "labels": [{"id": i, "message": m} for i, m in e.labels]}
    if isinstance(e, FileNotFoundError):   # 객체 유실 등 — UI 는 일반 오류로 표시
        return 409, {"ok": False, "error": str(e)}
    if isinstance(e, (NotFound, KeyError)):
        return 404, {"error": str(e)}
    if isinstance(e, ValueError):   # json.JSONDecodeError 포함
        return 400, {"error": str(e)}
    return 500, {"error": f"{type(e).__name__}: {e}"}


_thumb_cache: dict[str, tuple[str, bytes] | None] = {}
_thumb_lock = threading.Lock()


def _read_thumb(p: Path) -> tuple[str, bytes] | None:
    try:
        pkg = read_package(p, thumbnails=True)
        for t in pkg.thumbnails:
            if t.fmt in ("png", "jpeg"):
                return ("image/" + t.fmt, t.data)
    except Exception:
        return None
    return None


def api_thumb(store: Store, sha: str = "", rel: str = "") -> tuple[str, bytes] | None:
    """(content-type, bytes) 또는 None. rel 은 작업 트리(Content/<rel>)의 현재 파일.

    결과(없음 포함)는 메모리 캐시. rel 캐시 키는 rel+mtime+size, 경로 탈출은 None.
    """
    if rel:
        r = rel.replace("\\", "/").strip("/")
        base = store.cfg.content.resolve()
        try:
            p = (base / r).resolve()
            p.relative_to(base)
        except (ValueError, OSError):
            return None
        if not p.is_file():
            return None
        stt = p.stat()
        key = f"rel:{p}:{stt.st_mtime_ns}:{stt.st_size}"
        with _thumb_lock:
            if key in _thumb_cache:
                return _thumb_cache[key]
        result = _read_thumb(p)
        with _thumb_lock:
            _thumb_cache[key] = result
        return result
    if not sha or any(c not in "0123456789abcdef" for c in sha.lower()):
        return None
    with _thumb_lock:
        if sha in _thumb_cache:
            return _thumb_cache[sha]
    p = store.object_path(sha)
    result = _read_thumb(p) if p.exists() else None
    with _thumb_lock:
        _thumb_cache[sha] = result
    return result


# ---- HTTP ----
class NotFound(Exception):
    pass


def make_handler(cfg: Config, control=None):
    """control 은 선택적 데몬 컨트롤 객체(daemon.DaemonControl). 없으면 serve 단독."""
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
                elif path == "/api/info":
                    self._json(self._run(api_info))
                elif path == "/api/log":
                    self._json(self._run(api_log))
                elif path == "/api/daemon":
                    self._json(api_daemon_get(control))
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
                elif path == "/api/metadiff":
                    a_sha = q.get("a", [""])[0]
                    b_sha = q.get("b", [""])[0]
                    self._json(self._run(lambda st: api_metadiff(st, a_sha, b_sha)))
                elif path == "/api/search":
                    term = q.get("q", [""])[0]
                    self._json(self._run(lambda st: api_search(st, term)))
                elif path == "/api/thumb":
                    sha = q.get("sha", [""])[0]
                    trel = q.get("rel", [""])[0]
                    r = self._run(lambda st: api_thumb(st, sha, trel))
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
                if u.path == "/api/daemon":
                    self._json(api_daemon_post(control, str(body.get("action", ""))))
                elif u.path == "/api/snap":
                    message = str(body.get("message", "")).strip()
                    if not message:
                        raise ValueError("message 필요")
                    only = body.get("only") or []
                    if not isinstance(only, list):
                        raise ValueError("only 는 rel 목록")
                    self._json(self._run(lambda st: api_snap_create(st, message, only)))
                elif u.path == "/api/squash":
                    ids = body.get("ids") or []
                    if not isinstance(ids, list):
                        raise ValueError("ids 는 스냅샷 id 목록")
                    msg = str(body.get("message", "")).strip()
                    inc = bool(body.get("include_labels", False))
                    self._json(self._run(lambda st: api_squash(st, ids, msg, inc)))
                elif u.path == "/api/uediff":
                    rel = str(body.get("rel", "")).strip()
                    a_sha = str(body.get("a", "")).strip()
                    b_sha = str(body.get("b", "") or "").strip()
                    if not rel or not a_sha:
                        raise ValueError("rel, a 필요")
                    self._json(self._run(lambda st: api_uediff(st, rel, a_sha, b_sha)))
                elif u.path == "/api/prune":
                    dry = bool(body.get("dry_run", True))
                    self._json(self._run(lambda st: api_prune(st, dry)))
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
