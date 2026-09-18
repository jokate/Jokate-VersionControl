"""
jokate 웹 API 클라이언트 (UE 에디터 안팎 공용, 표준 라이브러리만: unreal 을 import 하지 않음)

- base_url(project_dir): <project>/.jokate/config.toml 의 [web] port(기본 8765) → http://127.0.0.1:<port>
- head_id(base)        : /api/log 첫 항목 id 또는 None
- status(base)         : /api/status 의 json
- snap_only(base, message, rels) : POST /api/snap {message, only}
- restore_assets(base, sid, rels, discard_dirty=False) : POST /api/restore/<sid> → (status_code, json)
  409(dirty) 도 예외 없이 (409, {...}) 로 돌려준다. 연결 실패는 ConnectionError.
- restore_preview(base, sid, rels) : GET /api/restore/<sid>?asset=... 드라이런 → (status_code, json)
- format_preview / preview_change_count / format_blocked / format_result : 모달 창에 쓸 사람 읽는 문자열
- package_to_rel('/Game/Blueprint/BP_Monster', content_dir) → 'Blueprint/BP_Monster.uasset' (.umap 이 있으면 .umap)
"""
import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_PORT = 8765
TIMEOUT = 300.0  # restore 는 에디터 브릿지 왕복이 있어 길 수 있음


def read_port(project_dir):
    """<project>/.jokate/config.toml 의 [web] port. 파일/항목 없으면 DEFAULT_PORT. tomllib 없는 UE 파이썬도 고려해 정규식 파싱."""
    path = os.path.join(str(project_dir), ".jokate", "config.toml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return DEFAULT_PORT
    section = None
    for line in text.splitlines():
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        m = re.match(r"^\[(.+?)\]$", s)
        if m:
            section = m.group(1).strip()
            continue
        if section == "web":
            m = re.match(r"^port\s*=\s*(\d+)$", s)
            if m:
                return int(m.group(1))
    return DEFAULT_PORT


def base_url(project_dir):
    return "http://127.0.0.1:%d" % read_port(project_dir)


class ConnectionError(Exception):  # noqa: A001 - 서버 안 떠 있음
    pass


def _call(base, path, body=None, timeout=None):
    """(status_code, json). HTTP 오류 상태도 본문이 json 이면 그대로 돌려준다."""
    if timeout is None:
        timeout = TIMEOUT
    url = base.rstrip("/") + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw or "null")
        except ValueError:
            return e.code, {"ok": False, "error": raw or str(e)}
    except (urllib.error.URLError, OSError) as e:
        raise ConnectionError("jokate 서버에 연결할 수 없음 (%s): %s" % (url, getattr(e, "reason", e)))


def head_id(base):
    code, log = _call(base, "/api/log")
    if code != 200 or not log:
        return None
    return log[0].get("id")


def daemon_status(base, timeout=2.0):
    """GET /api/daemon → (code, json). 서버가 없으면 ConnectionError (에디터 자동 실행 판정용)."""
    return _call(base, "/api/daemon", timeout=timeout)


def status(base):
    code, j = _call(base, "/api/status")
    if code != 200:
        raise RuntimeError("status 실패 %s: %s" % (code, j))
    return j


def snap_only(base, message, rels):
    return _call(base, "/api/snap", {"message": message, "only": list(rels)})


def restore_assets(base, sid, rels, discard_dirty=False):
    return _call(base, "/api/restore/%d" % int(sid), {"assets": list(rels), "discard_dirty": bool(discard_dirty)})


def restore_preview(base, sid, rels):
    """드라이런: GET /api/restore/<sid>?asset=<rel>&asset=... → (status_code, json). 적용하지 않는다."""
    from urllib.parse import urlencode
    q = urlencode([("asset", r) for r in rels])
    return _call(base, "/api/restore/%d%s" % (int(sid), ("?" + q) if q else ""))


# ---- 사람이 읽는 문자열 (에디터 모달 창용) ----
def _rows(preview):
    """드라이런 json → [(마커, 텍스트, 리세이브여부)] (M 수정 / A 부활 / R 이동 / D 삭제)."""
    d = (preview or {}).get("diff") or {}
    out = []
    for p in d.get("modified") or []:
        new = p.get("new") or {}
        out.append(("M", new.get("rel") or (p.get("old") or {}).get("rel") or "?", bool(new.get("noise"))))
    for e in d.get("added") or []:
        out.append(("A", e.get("rel") or "?", False))
    for p in d.get("moved") or []:
        out.append(("R", "%s → %s" % ((p.get("old") or {}).get("rel"), (p.get("new") or {}).get("rel")), False))
    for e in d.get("deleted") or []:
        out.append(("D", e.get("rel") or "?", False))
    return out


def preview_change_count(preview):
    """드라이런에서 실제로 바뀔 애셋 수 (리세이브만인 것도 파일이 다시 쓰이므로 포함)."""
    return len(_rows(preview))


def _warnings(preview):
    out = []
    for key, label in (("broken", "참조 끊김"), ("dependents", "이 애셋을 쓰는 곳")):
        for w in (preview or {}).get(key) or []:
            out.append("%s: %s ← %s" % (label, w.get("rel"), w.get("dep")))
    return out


def format_preview(preview, sid, max_rows=12):
    """되돌리기 확인 창 본문."""
    snap = (preview or {}).get("snapshot") or {}
    msg = snap.get("message") or "(메시지 없음)"
    lines = ["#%s %s 상태로 되돌립니다" % (snap.get("id", sid), msg)]
    counts = ((preview or {}).get("diff") or {}).get("counts") or {}
    parts = []
    for key, label in (("modified", "수정"), ("added", "부활"), ("moved", "이동"), ("deleted", "삭제")):
        if counts.get(key):
            parts.append("%s %d" % (label, counts[key]))
    if counts.get("resave"):
        parts.append("리세이브만 %d" % counts["resave"])
    lines.append(" · ".join(parts) if parts else "바뀌는 애셋 없음")
    lines.append("")
    rows = _rows(preview)
    for mark, text, noise in rows[:max_rows]:
        lines.append("%s %s%s" % (mark, text, " (리세이브만)" if noise else ""))
    if len(rows) > max_rows:
        lines.append("… 외 %d개" % (len(rows) - max_rows))
    warns = _warnings(preview)
    if warns:
        lines.append("")
        lines.append("참조 경고 %d건" % len(warns))
        lines.extend("  " + w for w in warns[:5])
    lines.append("")
    lines.append("되돌리기 직전 안전 스냅샷이 자동 생성됩니다.")
    return "\n".join(lines)


def format_blocked(body):
    """409 본문 → 저장 안 된 패키지 안내."""
    body = body or {}
    dirty = list(body.get("dirty") or [])
    lines = ["되돌리기가 막혔습니다: %s" % body.get("error", "저장하지 않은 변경이 있습니다")]
    if dirty:
        lines.append("저장 안 된 패키지 %d개:" % len(dirty))
        lines.extend("  " + p for p in dirty[:10])
        if len(dirty) > 10:
            lines.append("  … 외 %d개" % (len(dirty) - 10))
    return "\n".join(lines)


def format_result(body):
    """성공 본문 → '롤백 완료 #N · 안전 스냅샷 #M · 복사 a · 삭제 b'."""
    body = body or {}
    return "롤백 완료 #%s · 안전 스냅샷 #%s · 복사 %s · 삭제 %s" % (
        (body.get("result") or {}).get("id"), (body.get("safety") or {}).get("id"),
        body.get("written", 0), body.get("deleted", 0))


def package_to_rel(pkg_name, content_dir):
    """'/Game/A/B' → 'A/B.uasset' (디스크에 B.umap 이 있으면 'A/B.umap'). /Game 밖 패키지는 None."""
    pkg = str(pkg_name).replace("\\", "/")
    if not pkg.startswith("/Game/"):
        return None
    rel = pkg[len("/Game/"):].strip("/")
    if not rel:
        return None
    if os.path.exists(os.path.join(str(content_dir), rel + ".umap")):
        return rel + ".umap"
    return rel + ".uasset"


def web_url(base, asset_rel=None, view=None):
    from urllib.parse import urlencode
    q = {}
    if asset_rel:
        q["asset"] = asset_rel
    if view:
        q["view"] = view
    return base.rstrip("/") + "/" + ("?" + urlencode(q) if q else "")
