"""
jokate 에디터 브릿지 (UE 에디터 Python 환경에서 실행)

<project>/Content/Python/jokate_bridge.py 로 복사되어 init_unreal.py 의 `import jokate_bridge` 로 로드된다.
1초마다 <project>/.jokate/bridge/heartbeat.json 을 갱신하고 request.json 이 있으면 처리해 response-<id>.json 을 쓴다.

op 'dirty'  : packages 중 에디터에서 dirty(저장 안 됨)인 패키지 이름 목록 → {ok, dirty:[...]}
op 'reload' : args.discard_dirty 가 아니고 dirty 대상이 있으면 {ok:false, dirty:[...]}.
              아니면 존재하는 패키지를 load_package → reload_packages(ASSUME_POSITIVE),
              대상 폴더를 scan_paths_synchronous(force_rescan) 로 추가/삭제 반영 → {ok, reloaded:n}
"""
import json
import os
import time
import traceback

import unreal

POLL_INTERVAL = 1.0

_project_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
BRIDGE_DIR = os.path.join(_project_dir, ".jokate", "bridge")
_last_poll = 0.0
_tick_handle = None


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _dirty_names(packages):
    """요청 packages 중 dirty 인 패키지 이름."""
    want = set(packages)
    out = []
    dirty = list(unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages())
    try:
        dirty += list(unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages())
    except Exception:
        pass
    for p in dirty:
        name = p.get_name()
        if name in want and name not in out:
            out.append(name)
    return out


def _op_dirty(packages, args):
    return {"ok": True, "dirty": _dirty_names(packages)}


def _op_reload(packages, args):
    if not args.get("discard_dirty"):
        dirty = _dirty_names(packages)
        if dirty:
            return {"ok": False, "dirty": dirty, "error": "dirty 패키지 있음"}
    loaded = []
    for name in packages:
        try:
            pkg = unreal.load_package(name)
        except Exception:
            pkg = None
        if pkg is not None:
            loaded.append(pkg)
    if loaded:
        unreal.EditorLoadingAndSavingUtils.reload_packages(
            loaded, unreal.ReloadPackagesInteractionMode.ASSUME_POSITIVE)
    folders = sorted({name.rsplit("/", 1)[0] for name in packages if "/" in name})
    if folders:
        unreal.AssetRegistryHelpers.get_asset_registry().scan_paths_synchronous(folders, force_rescan=True)
    return {"ok": True, "reloaded": len(loaded), "scanned": folders}


_OPS = {"dirty": _op_dirty, "reload": _op_reload}


def _handle_request():
    req_path = os.path.join(BRIDGE_DIR, "request.json")
    if not os.path.exists(req_path):
        return
    try:
        with open(req_path, "r", encoding="utf-8") as f:
            req = json.load(f)
    except (OSError, ValueError):
        return  # 아직 쓰는 중이면 다음 틱에
    try:
        os.remove(req_path)
    except OSError:
        pass
    rid = req.get("id", "")
    op = req.get("op", "")
    packages = list(req.get("packages") or [])
    args = dict(req.get("args") or {})
    try:
        fn = _OPS.get(op)
        if fn is None:
            resp = {"ok": False, "error": "unknown op: %s" % op}
        else:
            resp = fn(packages, args)
    except Exception as e:  # noqa: BLE001
        resp = {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "trace": traceback.format_exc()}
    resp["id"] = rid
    resp["op"] = op
    _write_json(os.path.join(BRIDGE_DIR, "response-%s.json" % rid), resp)
    unreal.log("[jokate] %s → ok=%s" % (op, resp.get("ok")))


def _tick(delta_seconds):
    global _last_poll
    now = time.time()
    if now - _last_poll < POLL_INTERVAL:
        return
    _last_poll = now
    try:
        os.makedirs(BRIDGE_DIR, exist_ok=True)
        _write_json(os.path.join(BRIDGE_DIR, "heartbeat.json"), {"ts": now})
        _handle_request()
    except Exception:  # noqa: BLE001
        unreal.log_warning("[jokate] bridge tick error:\n" + traceback.format_exc())


def start():
    global _tick_handle
    stop()
    _tick_handle = unreal.register_slate_post_tick_callback(_tick)
    unreal.log("[jokate] bridge started: %s" % BRIDGE_DIR)


def stop():
    global _tick_handle
    if _tick_handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(_tick_handle)
        except Exception:
            pass
        _tick_handle = None


start()
