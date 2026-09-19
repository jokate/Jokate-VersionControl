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
  GET  /api/status                   baseline(마지막으로 올린 상태) 대비 올리지 않은 변경 {diff}
  GET  /api/revert?asset=<rel>       baseline 으로 되돌리기 드라이런 (적용 없음)
  POST /api/revert {assets?, discard_dirty?}  baseline 으로 되돌리기 적용 (409 규칙은 restore 와 동일)
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

UE 리비전 컨트롤 프로바이더용
  GET  /api/ping                     {ok, project, root, content, port, build, api}
  POST /api/states {rels?:[rel]}     {head_fix, states:{rel:{state,tier,sha,baseline_sha,size,cls,noise}}}
  GET  /api/history?rel=&limit=50    확정 버전 이력 [{id,revision,message,ts,time,sha,size,action}]
  POST /api/extract {rel, sha}       Saved/JokateDiff/<sha8>/<이름> 로 꺼내기 → {ok, path}
  POST /api/confirm|/api/discard 에 editor_managed:bool (true 면 브릿지 호출 안 함)

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
from .config import ASSET_EXTS, Config
from .scan import _scan_one
from .store import (Diff, RestoreBlocked, Snapshot, SquashHasLabels, Store, TreeEntry,
                    dep_pkg, deps_change, diff_trees, format_ts)
from .uasset import is_resave_only, read_package

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
    return {"id": s.id, "parent": s.parent, "kind": s.kind, "role": s.role, "message": s.message,
            "ts": s.ts, "time": format_ts(s.ts)}


def _pair(o: TreeEntry, n: TreeEntry, known: set[str] | None) -> dict:
    """modified·moved 한 쌍 + 참조 변화(추가/제거, 추가분 중 프로젝트에 없는 것)."""
    added, removed = deps_change(o, n)
    missing = [p for p in added if known is not None and p.startswith("/Game/") and p not in known]
    return {"old": _entry(o), "new": _entry(n),
            "deps_added": added, "deps_removed": removed, "deps_missing": missing}


def _pkgs(rels) -> set[str]:
    """rel 목록 → /Game/ 패키지 집합 (참조 대상이 프로젝트에 있는지 판정용)."""
    return {dep_pkg(r) for r in rels}


def _pkgs_after(tree: dict[str, TreeEntry], d: Diff) -> set[str]:
    """tree 에 diff 를 적용한 뒤 남는 패키지 집합."""
    rels = set(tree) - {e.rel for e in d.deleted} - {o.rel for o, _ in d.moved}
    rels |= {e.rel for e in d.added} | {n.rel for _, n in d.moved}
    return _pkgs(rels)


def _diff(d: Diff, known: set[str] | None = None) -> dict:
    return {
        "added": [_entry(e) for e in d.added],
        "modified": [_pair(o, n, known) for o, n in d.modified],
        "moved": [_pair(o, n, known) for o, n in d.moved],
        "deleted": [_entry(e) for e in d.deleted],
        "counts": _counts(d),
        "by_class": {cls: dict(c) for cls, c in sorted(d.by_class().items())},
        "all_noise": d.all_noise,
    }


# ---- API 로직 (서버 독립) ----
def api_info(store: Store) -> dict:
    """상단 요약 + pending(올리지 않은 변경 수)·last_label(마지막으로 올린 스냅샷)."""
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
    pending = sum(_counts(store.status()).values())
    fix = store.head_fix()
    last_label = _snapshot(fix) if fix else None
    n_fixes = store.db.execute("SELECT COUNT(*) FROM snapshots WHERE role='fix'").fetchone()[0]
    n_journal = store.db.execute("SELECT COUNT(*) FROM snapshots WHERE role<>'fix'").fetchone()[0]
    return {"project": store.cfg.root.name, "snapshots": n_snaps, "assets": n_assets,
            "objects": objects, "store_bytes": size, "last": _snapshot(head) if head else None,
            "pending": pending, "last_label": last_label,
            "fixes": n_fixes, "journal": n_journal,
            "vendor": list(store.cfg.vendor),
            "build": BUILD_ID, "build_disk": disk, "stale": disk != BUILD_ID}


def api_log(store: Store, role: str | None = None) -> list[dict]:
    """타임라인. role='fix'(확정 버전만)·'journal'(작업 중 기록만)·None/'all'(전부)."""
    want = role if role in ("fix", "journal") else None
    trees: dict[int | None, dict[str, TreeEntry]] = {None: {}}
    out = []
    for s in store.log(role=want):
        for sid in (s.id, s.parent):
            if sid not in trees:
                trees[sid] = store.tree(sid)
        up = store.uploaded_diff(s.id)     # 사용자가 올린 스냅샷은 '이번에 올린 것' 기준
        d = up if up is not None else diff_trees(trees[s.parent], trees[s.id])
        item = _snapshot(s)
        item["uploaded"] = up is not None
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
    s, d = store.show(sid)  # KeyError → 404 (올린 스냅샷이면 '이번에 올린 것')
    return {"snapshot": _snapshot(s), "diff": _diff(d, _pkgs(store.tree(sid))),
            "uploaded": store.uploaded_diff(sid) is not None}


def api_asset(store: Store, rel: str) -> dict:
    rel = rel.replace("\\", "/").strip("/")
    rows = store.db.execute(
        "SELECT s.id, s.kind, s.message, s.ts, t.sha, t.size, t.cls, t.deps "
        "FROM snapshots s LEFT JOIN tree t ON t.snapshot_id = s.id AND t.rel = ? "
        "ORDER BY s.id ASC", (rel,)).fetchall()
    versions = []
    prev_sha: str | None = None
    prev_deps: list[str] = []
    for sid, kind, message, ts, sha, size, cls, deps in rows:
        present = sha is not None
        cur_deps = json.loads(deps) if deps else []
        if present:
            state = "added" if prev_sha is None else ("modified" if sha != prev_sha else "same")
        else:
            state = "deleted" if prev_sha is not None else "absent"
        if state != "absent":
            # 직전 버전 대비 참조 변화 (삭제된 버전은 그 시점 참조가 통째로 사라진 것)
            d_add, d_rem = deps_change(TreeEntry(rel, "", 0, "", prev_deps),
                                       TreeEntry(rel, "", 0, "", cur_deps))
            versions.append({"id": sid, "kind": kind, "message": message, "ts": ts, "time": format_ts(ts),
                             "sha": sha, "size": size, "cls": cls or "?", "state": state,
                             "changed": state in ("added", "modified", "deleted"),
                             "deps_added": d_add, "deps_removed": d_rem,
                             "has_meta": bool(sha) and metamod.has_meta(store, sha)})
        prev_sha = sha
        prev_deps = cur_deps
    versions.reverse()
    return {"rel": rel, "versions": versions}


def api_restore(store: Store, sid: int, assets: list[str] | None = None) -> dict:
    plan = store.plan_restore(sid, assets or None)  # KeyError → 404
    return {"snapshot": _snapshot(plan.snapshot), "assets": assets or [],
            "diff": _diff(plan.diff, _pkgs(plan.result)),
            "broken": [{"rel": r, "dep": d} for r, d in plan.broken],
            "dependents": [{"rel": r, "dep": d} for r, d in plan.dependents]}


def api_status(store: Store) -> dict:
    """baseline(마지막으로 올린 상태) 대비 아직 올리지 않은 변경."""
    base = store.baseline()
    d = store.status()
    return {"diff": _diff(d, _pkgs_after(base, d))}


def api_confirm(store: Store, message: str, only: list[str] | None = None,
                editor_managed: bool = False) -> dict:
    """확정(confirm). only 가 비면 확정 안 된 변경 전부, 확정할 게 없으면 snapshot=None.

    응답의 cleared 는 이번 확정이 지운 작업 중 기록 수.
    editor_managed=True 면 에디터 브릿지를 쓰지 않는다(확정은 파일을 건드리지 않아 원래 안 쓴다 —
    UE 프로바이더가 discard 와 같은 형태로 보낼 수 있게 받아만 둔다).
    """
    only = [str(x) for x in (only or []) if str(x).strip()]
    r = store.confirm(message, only=only or None)
    snap, d, stored = r
    if snap is not None:
        metamod.capture_for_snapshot(store, d)
    return {"snapshot": _snapshot(snap) if snap else None, "diff": _diff(d), "stored": stored,
            "cleared": r.cleared}


def api_snap_create(store: Store, message: str, only: list[str] | None = None) -> dict:
    """api_confirm 의 옛 이름(별칭)."""
    return api_confirm(store, message, only)


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
    kind = b.get("kind", "")
    if kind == "Text":
        return {"available": True, "missing": [], "kind": "Text", "cls": b.get("cls", ""),
                "props": metamod.summarize_props(a, b), "diff": metamod.diff_text(a, b)}
    return {"available": True, "missing": [], "kind": kind,
            "row_struct": b.get("row_struct", ""), "diff": metamod.diff_tables(a, b)}


def api_restore_apply(store: Store, sid: int, assets: list[str] | None = None,
                      discard_dirty: bool = False) -> dict:
    """plan_restore → apply_restore. RestoreBlocked(→409)/KeyError(→404) 는 호출자가 처리."""
    plan = store.plan_restore(sid, assets or None)
    r = store.apply_restore(plan, discard_dirty=discard_dirty)
    return {"ok": True, "safety": _snapshot(r.safety), "result": _snapshot(r.result),
            "written": r.written, "deleted": r.deleted, "reloaded": r.reloaded,
            "safety_created": r.safety_created}


def api_revert(store: Store, assets: list[str] | None = None) -> dict:
    """baseline(마지막으로 올린 상태)으로 되돌릴 계획(드라이런). /api/restore 와 같은 형태."""
    plan = store.plan_revert_to_baseline(assets or None)
    return {"snapshot": _snapshot(plan.snapshot), "assets": assets or [],
            "diff": _diff(plan.diff, _pkgs(plan.result)),
            "broken": [{"rel": r, "dep": d} for r, d in plan.broken],
            "dependents": [{"rel": r, "dep": d} for r, d in plan.dependents]}


def api_discard_apply(store: Store, assets: list[str] | None = None,
                      discard_dirty: bool = False, editor_managed: bool = False) -> dict:
    """변경 버리기 적용(마지막 확정 상태로). RestoreBlocked(→409) 규칙은 /api/restore 와 동일.

    응답의 undo 는 남긴 '실행 취소 지점'(없으면 None), cleared 는 지운 작업 중 기록 수.
    editor_managed=True 면 에디터 브릿지 호출을 하지 않는다(UE 프로바이더가 직접 리로드).
    """
    r = store.revert_to_baseline(assets or None, discard_dirty=discard_dirty,
                                 editor_managed=editor_managed)
    return {"ok": True, "safety": _snapshot(r.safety), "result": _snapshot(r.result),
            "written": r.written, "deleted": r.deleted, "reloaded": r.reloaded,
            "safety_created": r.safety_created,
            "undo": _snapshot(r.undo) if r.undo is not None else None, "cleared": r.cleared}


def api_discard(store: Store, assets: list[str] | None = None) -> dict:
    """api_revert(변경 버리기 드라이런) 의 새 이름."""
    return api_revert(store, assets)


def api_revert_apply(store: Store, assets: list[str] | None = None,
                     discard_dirty: bool = False) -> dict:
    """api_discard_apply 의 옛 이름(별칭)."""
    return api_discard_apply(store, assets, discard_dirty)


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


def api_uediff_plan(store: Store) -> dict:
    """diff 를 열면 어떤 방식이 될지 미리 알려준다 → {ok, mode, editor_running, bridge, hint}."""
    p = uediffmod.plan_diff(store)
    return {"ok": True, **p}


# ---- UE 리비전 컨트롤 프로바이더용 API ----
API_VERSION = 1
# (rel, size, mtime) → (sha, cls) — 연속 호출에서 같은 파일을 다시 해시하지 않기 위한 짧은 캐시
_states_cache: dict[tuple[str, int, float], tuple[str, str]] = {}
_states_lock = threading.Lock()


def norm_rel(rel) -> str:
    """Content 기준 rel 정규화: 역슬래시 → 슬래시, 앞뒤 슬래시 제거."""
    return str(rel or "").replace("\\", "/").strip("/")


def _scan_cached(cfg: Config, rel: str) -> tuple[str, int, str] | None:
    """작업 트리의 rel → (sha, size, cls). 파일이 없으면 None. (rel,size,mtime) 로 캐시."""
    p = cfg.content / rel
    try:
        st = p.stat()
    except OSError:
        return None
    key = (rel, st.st_size, st.st_mtime)
    with _states_lock:
        hit = _states_cache.get(key)
    if hit is not None:
        return hit[0], st.st_size, hit[1]
    rec = _scan_one(cfg, p, "authored", True)
    with _states_lock:
        if len(_states_cache) > 4000:
            _states_cache.clear()
        _states_cache[key] = (rec.sha, rec.cls)
    return rec.sha, st.st_size, rec.cls


def _state_entry(store: Store, base: dict[str, TreeEntry], rel: str) -> dict:
    """rel 하나의 baseline 대비 상태."""
    cfg = store.cfg
    info = {"state": "untracked", "tier": "", "sha": "", "baseline_sha": "",
            "size": 0, "cls": "", "noise": False}
    if not rel or ".." in rel.split("/") or ":" in rel:      # 경로 탈출·절대경로
        return info
    if os.path.splitext(rel)[1].lower() not in ASSET_EXTS:
        return info
    tier = cfg.tier_of(Path(rel))
    if tier != "authored":                                   # vendor·ignore 는 추적 안 함
        info["tier"] = tier or ""
        return info
    info["tier"] = "authored"
    b = base.get(rel)
    info["baseline_sha"] = b.sha if b else ""
    cur = _scan_cached(cfg, rel)
    if cur is None:
        if b is None:
            info["state"] = "missing"
        else:
            info.update(state="deleted", size=b.size, cls=b.cls)
        return info
    sha, size, cls = cur
    info.update(sha=sha, size=size, cls=cls or (b.cls if b else ""))
    if b is None:
        info["state"] = "added"
    elif b.sha == sha:
        info["state"] = "clean"
    else:
        info["state"] = "modified"
        try:
            info["noise"] = bool(is_resave_only(store.object_path(b.sha), cfg.content / rel))
        except Exception:  # noqa: BLE001
            info["noise"] = False
    return info


def api_states(store: Store, rels: list[str] | None = None) -> dict:
    """baseline(마지막 확정 상태) 대비 각 rel 의 상태 → {head_fix, states}.

    state: clean | modified | added | deleted | untracked | missing (이동은 added+deleted).
    rels 가 비면 추적 대상 전체(baseline ∪ 작업 트리)를 훑는다.
    """
    base = store.baseline()
    if rels:
        want = [norm_rel(r) for r in rels]
    else:
        want = sorted(set(base) | {r.rel for r in store.scan_authored()})
    states = {rel: _state_entry(store, base, rel) for rel in want}
    fix = store.head_fix()
    head = {"id": fix.id, "message": fix.message, "time": format_ts(fix.ts)} if fix else None
    return {"head_fix": head, "states": states}


def api_history(store: Store, rel: str, limit: int = 50) -> list[dict]:
    """애셋의 '확정 버전' 이력(fix 스냅샷에서 sha 가 바뀐 지점)만, 최신순.

    revision 은 1부터 오름차순 번호, action 은 add|edit|delete. 작업 중 기록(journal)은 제외.
    """
    rel = norm_rel(rel)
    rows = store.db.execute(
        "SELECT s.id, s.message, s.ts, t.sha, t.size FROM snapshots s "
        "LEFT JOIN tree t ON t.snapshot_id = s.id AND t.rel = ? "
        "WHERE s.role='fix' ORDER BY s.id ASC", (rel,)).fetchall()
    out: list[dict] = []
    prev: str | None = None
    rev = 0
    for sid, message, ts, sha, size in rows:
        if sha == prev:
            continue
        if sha is None:
            action, sha_v, size_v = "delete", "", 0
        else:
            action, sha_v, size_v = ("add" if prev is None else "edit"), sha, int(size or 0)
        rev += 1
        out.append({"id": sid, "revision": rev, "message": message, "ts": ts,
                    "time": format_ts(ts), "sha": sha_v, "size": size_v, "action": action})
        prev = sha
    out.reverse()
    return out[:limit] if limit and limit > 0 else out


def api_extract(store: Store, rel: str, sha: str) -> dict:
    """버전을 <project>/Saved/JokateDiff/<sha8>/<이름> 으로 꺼낸다 → {ok, path}.

    ValueError(sha 형식 → 400) / KeyError(객체 없음 → 404) 는 호출자가 처리.
    """
    p = uediffmod.extract_to_saved(store, rel, sha)
    return {"ok": True, "path": p.as_posix()}


def api_ping(cfg: Config, port: int | None = None) -> dict:
    """이 데몬이 어느 프로젝트의 것인지 → {ok, project, root, content, port, build, api}."""
    return {"ok": True, "project": cfg.root.name, "root": cfg.root.as_posix(),
            "content": cfg.content.as_posix(), "port": int(port or cfg.port),
            "build": BUILD_ID, "api": API_VERSION}


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
    if isinstance(e, uediffmod.DiffBlocked):     # 에디터가 켜져 있어 새 에디터를 띄우지 않음
        return 409, {"ok": False, "error": str(e), "mode": "editor"}
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
                    role = q.get("role", ["all"])[0]
                    self._json(self._run(lambda st: api_log(st, role)))
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
                elif path in ("/api/revert", "/api/discard"):
                    assets = q.get("asset", [])
                    self._json(self._run(lambda st: api_discard(st, assets)))
                elif path == "/api/ping":
                    self._json(api_ping(cfg))
                elif path == "/api/history":
                    rel = q.get("rel", [""])[0]
                    if not rel:
                        raise ValueError("rel 필요")
                    limit = int(q.get("limit", ["50"])[0] or 50)
                    self._json(self._run(lambda st: api_history(st, rel, limit)))
                elif path == "/api/uediff/plan":
                    self._json(self._run(api_uediff_plan))
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
                elif u.path in ("/api/snap", "/api/confirm"):
                    message = str(body.get("message", "")).strip()
                    if not message:
                        raise ValueError("message 필요")
                    only = body.get("only") or []
                    if not isinstance(only, list):
                        raise ValueError("only 는 rel 목록")
                    managed = bool(body.get("editor_managed", False))
                    self._json(self._run(lambda st: api_confirm(st, message, only, managed)))
                elif u.path in ("/api/revert", "/api/discard"):
                    assets = body.get("assets") or []
                    if not isinstance(assets, list):
                        raise ValueError("assets 는 rel 목록")
                    discard = bool(body.get("discard_dirty", False))
                    managed = bool(body.get("editor_managed", False))
                    self._json(self._run(lambda st: api_discard_apply(
                        st, [str(x) for x in assets], discard, managed)))
                elif u.path == "/api/states":
                    rels = body.get("rels") or []
                    if not isinstance(rels, list):
                        raise ValueError("rels 는 rel 목록")
                    self._json(self._run(lambda st: api_states(st, [str(x) for x in rels])))
                elif u.path == "/api/extract":
                    rel = str(body.get("rel", "")).strip()
                    sha = str(body.get("sha", "")).strip()
                    if not rel or not sha:
                        raise ValueError("rel, sha 필요")
                    self._json(self._run(lambda st: api_extract(st, rel, sha)))
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
