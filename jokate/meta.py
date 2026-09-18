"""
의미 diff용 사이드카(메타) 저장

과거 버전 .uasset 을 에디터에 다시 로드하지 않는다. 스냅샷을 찍는 순간 브릿지가 살아 있으면
'지금 에디터에 로드된 현재 버전'의 내용을 JSON 으로 받아 그 파일의 sha 를 키로 저장한다.

- 경로: <project>/.jokate/store/meta/<sha[:2]>/<sha>.json
- 내용: {"kind": "DataTable", "row_struct": str, "columns": [...], "rows": {행이름: {열: "값"}}}
- 두 버전 모두 사이드카가 있으면 JSON 끼리 비교한다(diff_tables).
"""
from __future__ import annotations

import json
from pathlib import Path

META_CLASSES = {"DataTable"}


# ---- 저장/읽기 ----
def meta_root(store) -> Path:
    return store.cfg.state_dir / "store" / "meta"


def meta_path(store, sha: str) -> Path:
    return meta_root(store) / sha[:2] / (sha + ".json")


def has_meta(store, sha: str) -> bool:
    return bool(sha) and meta_path(store, sha).exists()


def load_meta(store, sha: str) -> dict | None:
    p = meta_path(store, sha)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_meta(store, sha: str, data: dict) -> Path:
    """원자적 쓰기."""
    p = meta_path(store, sha)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return p


# ---- 순수 diff ----
def _table(m: dict | None) -> tuple[list[str], dict[str, dict]]:
    if not m:
        return [], {}
    cols = [str(c) for c in (m.get("columns") or [])]
    rows = {str(k): dict(v or {}) for k, v in (m.get("rows") or {}).items()}
    return cols, rows


def diff_tables(a: dict | None, b: dict | None) -> dict:
    """DataTable 메타 두 개를 비교. 행·열 순서는 b 기준, 없는 값은 빈 문자열."""
    a_cols, a_rows = _table(a)
    b_cols, b_rows = _table(b)
    cols = list(b_cols) + [c for c in a_cols if c not in b_cols]
    out = {"columns_added": [c for c in b_cols if c not in a_cols],
           "columns_removed": [c for c in a_cols if c not in b_cols],
           "rows_added": [], "rows_removed": [], "rows_changed": [], "unchanged": 0}
    for name, row in b_rows.items():
        if name not in a_rows:
            out["rows_added"].append({"row": name, "cells": {c: str(row.get(c, "")) for c in b_cols}})
            continue
        old = a_rows[name]
        changed = []
        for c in cols:
            ov, nv = str(old.get(c, "")), str(row.get(c, ""))
            if ov != nv:
                changed.append({"col": c, "old": ov, "new": nv})
        if changed:
            out["rows_changed"].append({"row": name, "cells": changed})
        else:
            out["unchanged"] += 1
    for name, row in a_rows.items():
        if name not in b_rows:
            out["rows_removed"].append({"row": name, "cells": {c: str(row.get(c, "")) for c in a_cols}})
    return out


# ---- 수집 ----
def pkg_of_rel(rel: str) -> str:
    return "/Game/" + rel.replace("\\", "/").strip("/").rsplit(".", 1)[0]


def _file_sha(path: Path) -> str:
    from .scan import _hash_file
    try:
        return _hash_file(path)
    except OSError:
        return ""


def capture_meta(store, entries, request_fn=None) -> tuple[int, int]:
    """entries 중 메타 대상 클래스이고 사이드카가 아직 없는 것만 브릿지에서 받아 저장.

    반환 (저장 수, 건너뜀 수). 브릿지가 없거나 오류면 조용히 건너뛴다.
    응답을 받은 뒤 디스크 파일의 sha 가 entries 의 sha 와 여전히 같을 때만 저장한다.
    """
    targets = {}
    for e in entries or []:
        cls = getattr(e, "cls", "") or ""
        sha = getattr(e, "sha", "") or ""
        if cls not in META_CLASSES or not sha or has_meta(store, sha):
            continue
        targets[pkg_of_rel(e.rel)] = e
    if not targets:
        return 0, 0
    if request_fn is None:
        from . import bridge
        request_fn = bridge.request
    try:
        r = request_fn(store.cfg, "export_meta", sorted(targets))
    except Exception:  # noqa: BLE001  브릿지 없음·시간 초과 등
        return 0, len(targets)
    if not isinstance(r, dict) or not r.get("ok"):
        return 0, len(targets)
    got = r.get("meta") or {}
    saved = 0
    for pkg, e in targets.items():
        m = got.get(pkg)
        if not isinstance(m, dict):
            continue
        try:
            if _file_sha(store.cfg.content / e.rel) != e.sha:
                continue        # 그 사이 다시 저장됐다 → 버린다
            save_meta(store, e.sha, m)
            saved += 1
        except Exception:  # noqa: BLE001
            continue
    return saved, len(targets) - saved


def capture_for_snapshot(store, diff, request_fn=None) -> tuple[int, int]:
    """스냅샷 diff 에서 바뀐 항목(추가·수정·이동)에 대해 capture_meta."""
    entries = list(getattr(diff, "added", []) or [])
    for _, n in list(getattr(diff, "modified", []) or []) + list(getattr(diff, "moved", []) or []):
        entries.append(n)
    try:
        return capture_meta(store, entries, request_fn)
    except Exception:  # noqa: BLE001
        return 0, 0
