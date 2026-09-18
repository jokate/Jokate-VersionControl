"""
jokate 웹 API 클라이언트 (UE 에디터 안팎 공용, 표준 라이브러리만: unreal 을 import 하지 않음)

- base_url(project_dir): <project>/.jokate/config.toml 의 [web] port(기본 8765) → http://127.0.0.1:<port>
- head_id(base)        : /api/log 첫 항목 id 또는 None
- status(base)         : /api/status 의 json
- snap_only(base, message, rels) : POST /api/snap {message, only}
- restore_assets(base, sid, rels, discard_dirty=False) : POST /api/restore/<sid> → (status_code, json)
  409(dirty) 도 예외 없이 (409, {...}) 로 돌려준다. 연결 실패는 ConnectionError.
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


def _call(base, path, body=None):
    """(status_code, json). HTTP 오류 상태도 본문이 json 이면 그대로 돌려준다."""
    url = base.rstrip("/") + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
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


def status(base):
    code, j = _call(base, "/api/status")
    if code != 200:
        raise RuntimeError("status 실패 %s: %s" % (code, j))
    return j


def snap_only(base, message, rels):
    return _call(base, "/api/snap", {"message": message, "only": list(rels)})


def restore_assets(base, sid, rels, discard_dirty=False):
    return _call(base, "/api/restore/%d" % int(sid), {"assets": list(rels), "discard_dirty": bool(discard_dirty)})


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
