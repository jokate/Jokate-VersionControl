"""
의미 diff용 사이드카(메타) 저장

과거 버전 .uasset 을 에디터에 다시 로드하지 않는다. 스냅샷을 찍는 순간 브릿지가 살아 있으면
'지금 에디터에 로드된 현재 버전'의 내용을 JSON 으로 받아 그 파일의 sha 를 키로 저장한다.

- 경로: <project>/.jokate/store/meta/<sha[:2]>/<sha>.json
- 내용(표): {"kind": "DataTable", "row_struct": str, "columns": [...], "rows": {행이름: {열: "값"}}}
- 내용(텍스트): {"kind": "Text", "cls": str, "text": "T3D 원문"}  ← DataAsset 등 일반 애셋
- 두 버전 모두 사이드카가 있으면 끼리 비교한다(diff_tables / diff_text·summarize_props).
"""
from __future__ import annotations

import difflib
import fnmatch
import json
import re
from pathlib import Path

TABLE_CLASSES = {"DataTable"}
META_CLASSES = TABLE_CLASSES          # 하위 호환 (표 방식 클래스)

# Text(T3D) 방식 기본 대상 — config.toml [meta] text_classes 로 덮어쓸 수 있다
DEFAULT_TEXT_CLASSES = ["*DataAsset", "CurveFloat", "CurveTable",
                        "InputAction", "InputMappingContext", "YS*"]
# 기본으로는 제외 (크고 의미 없는 줄이 많다). text_classes 에 직접 써 넣으면 허용한다
EXCLUDED_CLASSES = ["Blueprint", "*Blueprint", "Material*", "Texture*", "*Mesh",
                    "Anim*", "World", "Level", "*Sequence"]

MAX_TEXT_BYTES = 512 * 1024


def meta_kind(cls: str, text_classes=None) -> str:
    """클래스 이름 → '' | 'DataTable' | 'Text'."""
    cls = (cls or "").strip()
    if not cls:
        return ""
    if cls in TABLE_CLASSES:
        return "DataTable"
    pats = list(DEFAULT_TEXT_CLASSES if text_classes is None else text_classes)
    hit = [p for p in pats if fnmatch.fnmatch(cls, p)]
    if cls.endswith("DataAsset") and not hit:
        hit = ["*DataAsset"]
    if not hit:
        return ""
    # 기본 패턴에만 걸렸다면 제외 목록을 적용한다. 사용자가 직접 쓴 패턴은 그대로 허용
    if all(p in DEFAULT_TEXT_CLASSES for p in hit):
        for ex in EXCLUDED_CLASSES:
            if fnmatch.fnmatch(cls, ex):
                return ""
    return "Text"


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


# ---- 텍스트(T3D) 정규화·diff ----
_RE_GUID = re.compile(r"\b[0-9A-Fa-f]{32}\b")
_RE_PTR = re.compile(r"\b0x[0-9A-Fa-f]{8,16}\b")
_RE_EXPORT_PATH = re.compile(r'(ExportPath=)"[^"]*"')
_RE_TMP_NAME = re.compile(r"JokateMeta[/\\][^\s\"']+")


def normalize_t3d(text: str) -> str:
    """내보낼 때마다 달라지는 잡음을 지운 순수 텍스트. 'Begin/End Object' 줄은 유지."""
    if not text:
        return ""
    out = []
    for line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _RE_EXPORT_PATH.sub(r'\1"<PATH>"', line)
        line = _RE_TMP_NAME.sub("<TMP>", line)
        line = _RE_GUID.sub("<GUID>", line)
        line = _RE_PTR.sub("<PTR>", line)
        out.append(line.rstrip())
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _lines(m) -> list[str]:
    if isinstance(m, dict):
        m = m.get("text", "")
    return normalize_t3d(m or "").split("\n") if m else []


def diff_text(a, b, context: int = 3, max_lines: int = 400) -> dict:
    """T3D 두 개의 줄 diff. {lines:[{op,text,a_no,b_no}], added, removed, truncated}."""
    al, bl = _lines(a), _lines(b)
    sm = difflib.SequenceMatcher(None, al, bl, autojunk=False)
    lines: list[dict] = []
    added = removed = 0
    blocks = sm.get_opcodes()
    for k, (tag, i1, i2, j1, j2) in enumerate(blocks):
        if tag == "equal":
            idx = list(range(i1, i2))
            if len(idx) > context * 2:
                keep = ([] if k == 0 else idx[:context]) + \
                       ([] if k == len(blocks) - 1 else idx[-context:])
            else:
                keep = idx
            for i in keep:
                lines.append({"op": " ", "text": al[i], "a_no": i + 1, "b_no": j1 + (i - i1) + 1})
            continue
        for i in range(i1, i2):
            lines.append({"op": "-", "text": al[i], "a_no": i + 1, "b_no": 0})
            removed += 1
        for j in range(j1, j2):
            lines.append({"op": "+", "text": bl[j], "a_no": 0, "b_no": j + 1})
            added += 1
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
    return {"lines": lines, "added": added, "removed": removed, "truncated": truncated}


def _props(m) -> dict[str, str]:
    """'   Key(0)=Value' 줄들을 {키: 값} 으로. Begin/End Object 줄은 건너뛴다."""
    out: dict[str, str] = {}
    for line in _lines(m):
        s = line.strip()
        if not s or s.startswith("Begin Object") or s.startswith("End Object"):
            continue
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        if not key:
            continue
        out[key] = val.strip()
    return out


def summarize_props(a, b) -> dict:
    """속성 단위 요약. {changed:[{key,old,new}], added:[{key,new}], removed:[{key,old}]}."""
    pa, pb = _props(a), _props(b)
    out: dict[str, list] = {"changed": [], "added": [], "removed": []}
    for k, v in pb.items():
        if k not in pa:
            out["added"].append({"key": k, "new": v})
        elif pa[k] != v:
            out["changed"].append({"key": k, "old": pa[k], "new": v})
    for k, v in pa.items():
        if k not in pb:
            out["removed"].append({"key": k, "old": v})
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
    text_classes = list(getattr(store.cfg, "text_classes", None) or DEFAULT_TEXT_CLASSES)
    targets = {}
    for e in entries or []:
        cls = getattr(e, "cls", "") or ""
        sha = getattr(e, "sha", "") or ""
        if not meta_kind(cls, text_classes) or not sha or has_meta(store, sha):
            continue
        targets[pkg_of_rel(e.rel)] = e
    if not targets:
        return 0, 0
    if request_fn is None:
        from . import bridge
        request_fn = bridge.request
    try:
        r = request_fn(store.cfg, "export_meta", sorted(targets),
                       {"text_classes": text_classes, "max_bytes": MAX_TEXT_BYTES})
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
