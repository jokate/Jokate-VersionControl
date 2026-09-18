"""
jokate 에디터 브릿지 (UE 에디터 Python 환경에서 실행)

<project>/Content/Python/jokate_bridge.py 로 복사되어 init_unreal.py 의 `import jokate_bridge` 로 로드된다.
1초마다 <project>/.jokate/bridge/heartbeat.json 을 갱신하고 request.json 이 있으면 처리해 response-<id>.json 을 쓴다.

op 'dirty'  : packages 중 에디터에서 dirty(저장 안 됨)인 패키지 이름 목록 → {ok, dirty:[...]}
op 'reload' : args.discard_dirty 가 아니고 dirty 대상이 있으면 {ok:false, dirty:[...]}.
              아니면 존재하는 패키지를 load_package → reload_packages(ASSUME_POSITIVE),
              대상 폴더를 scan_paths_synchronous(force_rescan) 로 추가/삭제 반영 → {ok, reloaded:n}

콘텐츠 브라우저 애셋 우클릭 → 'Jokate' 서브메뉴 (올리기 / 마지막 스냅샷으로 되돌리기 / 히스토리·변경사항 웹).
HTTP 는 반드시 백그라운드 스레드에서: 되돌리기 요청은 서버가 이 브릿지(같은 에디터 틱)에 dirty/reload 를
물어보므로 게임 스레드에서 동기로 부르면 데드락. 결과는 _RESULTS 큐 → _tick 에서 unreal.log 로 보고.
"""
import json
import os
import queue
import threading
import time
import traceback

import unreal

try:
    import jokate_client as _client  # 같은 Content/Python 에 함께 설치됨
except ImportError:  # 개발 중 저장소에서 바로 로드한 경우
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("jokate_client", os.path.join(os.path.dirname(os.path.abspath(__file__)), "jokate_client.py"))
    _client = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_client)

POLL_INTERVAL = 1.0

_project_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
CONTENT_DIR = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())
BRIDGE_DIR = os.path.join(_project_dir, ".jokate", "bridge")
_last_poll = 0.0
_tick_handle = None
_RESULTS = queue.Queue()  # (level, message) — 워커 스레드가 넣고 _tick 이 게임 스레드에서 로그
_menu_registered = False
SERVE_HINT = "먼저 python -m jokate serve <프로젝트> 를 실행하세요"


# ---- 콘텐츠 브라우저 메뉴 ----
def _selected():
    """선택 애셋 → [(패키지명, 애셋명, rel)] (/Game 밖 · 변환 실패는 제외)."""
    out = []
    for a in unreal.EditorUtilityLibrary.get_selected_assets():
        pkg = a.get_outermost().get_name()
        rel = _client.package_to_rel(pkg, CONTENT_DIR)
        if rel:
            out.append((pkg, a.get_name(), rel))
    return out


def _report(level, msg):
    _RESULTS.put((level, msg))


def _run_bg(fn, *args):
    def work():
        try:
            fn(*args)
        except _client.ConnectionError as e:
            _report("warn", "[jokate] %s — %s" % (SERVE_HINT, e))
        except Exception:  # noqa: BLE001
            _report("warn", "[jokate] 오류:\n" + traceback.format_exc())
    threading.Thread(target=work, daemon=True).start()


def _do_snap(base, names, rels):
    message = "에디터에서 올림: %s %d개" % (", ".join(names[:5]) + (" …" if len(names) > 5 else ""), len(names))
    code, j = _client.snap_only(base, message, rels)
    if code == 200 and j.get("snapshot"):
        _report("log", "[jokate] 스냅샷 #%s 생성 (%d개)" % (j["snapshot"]["id"], len(rels)))
    elif code == 200:
        _report("log", "[jokate] 변경 없음 — 스냅샷을 만들지 않음")
    else:
        _report("warn", "[jokate] 올리기 실패 %s: %s" % (code, j.get("error", j)))


def _do_restore(base, rels):
    sid = _client.head_id(base)
    if sid is None:
        _report("warn", "[jokate] 스냅샷이 없어 되돌릴 수 없음")
        return
    code, j = _client.restore_assets(base, sid, rels)
    if code == 200 and j.get("ok"):
        _report("log", "[jokate] 롤백 완료 #%s (안전 스냅샷 #%s, 복사 %s, 삭제 %s)" % (
            j.get("result", {}).get("id"), j.get("safety", {}).get("id"), j.get("written"), j.get("deleted")))
    elif code == 409:
        dirty = j.get("dirty") or []
        _report("warn", "[jokate] 되돌리기 차단: %s\n  저장 안 된 패키지 %d개:\n    %s\n  저장 후 다시 시도" % (
            j.get("error", "dirty"), len(dirty), "\n    ".join(dirty) or "-"))
    else:
        _report("warn", "[jokate] 되돌리기 실패 %s: %s" % (code, j.get("error", j)))


def _open_web(asset_rel=None, view=None):
    url = _client.web_url(_client.base_url(_project_dir), asset_rel, view)
    unreal.log("[jokate] 브라우저 열기: %s" % url)
    os.startfile(url)  # noqa: S606


def _action(kind):
    base = _client.base_url(_project_dir)
    sel = _selected()
    if kind == "status":
        _open_web(view="status")
        return
    if not sel:
        unreal.log_warning("[jokate] /Game 아래의 애셋을 선택하세요")
        return
    names = [n for _, n, _ in sel]
    rels = [r for _, _, r in sel]
    if kind == "snap":
        unreal.log("[jokate] 올리는 중… (%d개)" % len(rels))
        _run_bg(_do_snap, base, names, rels)
    elif kind == "restore":
        unreal.log("[jokate] 마지막 스냅샷 상태로 되돌리는 중… (%d개) — 저장 안 된 변경이 있으면 차단됩니다" % len(rels))
        _run_bg(_do_restore, base, rels)
    elif kind == "history":
        _open_web(asset_rel=rels[0])


MENU_ITEMS = [
    ("snap", "선택한 애셋 올리기(스냅샷)", "선택한 애셋만 부분 스냅샷으로 올립니다"),
    ("restore", "선택한 애셋을 마지막 스냅샷 상태로 되돌리기", "저장 안 된 변경이 있으면 차단(로그 참고)"),
    ("history", "히스토리 열기(웹)", "첫 번째 선택 애셋의 버전 히스토리를 브라우저로"),
    ("status", "현재 변경사항 보기(웹)", "HEAD 대비 올리지 않은 변경"),
]


@unreal.uclass()
class JokateMenuEntry(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        kind = str(self.data.name).split("_", 1)[-1]  # 'Jokate_snap' → 'snap'
        try:
            _action(kind)
        except Exception:  # noqa: BLE001
            unreal.log_warning("[jokate] 메뉴 오류:\n" + traceback.format_exc())


_entries = []  # GC 방지


def _register_menu():
    global _menu_registered
    if _menu_registered:
        return
    menus = unreal.ToolMenus.get()
    menu = menus.extend_menu("ContentBrowser.AssetContextMenu")
    if menu is None:
        unreal.log_warning("[jokate] ContentBrowser.AssetContextMenu 를 찾지 못함 — 메뉴 미등록")
        return
    sub = menu.add_sub_menu("ContentBrowser.AssetContextMenu", "AssetContextExploreMenuOptions", "Jokate", "Jokate",
                            "jokate 로컬 버전 관리")
    for kind, label, tip in MENU_ITEMS:
        e = JokateMenuEntry()
        e.init_entry("ContentBrowser.AssetContextMenu.Jokate", "ContentBrowser.AssetContextMenu.Jokate", "Jokate",
                     "Jokate_%s" % kind, label, tip)
        e.register_menu_entry()
        _entries.append(e)
    menus.refresh_all_widgets()
    _menu_registered = True
    unreal.log("[jokate] 콘텐츠 브라우저 메뉴 등록")


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
        while True:  # 워커 스레드 결과를 게임 스레드에서 로그
            try:
                level, msg = _RESULTS.get_nowait()
            except queue.Empty:
                break
            (unreal.log_warning if level == "warn" else unreal.log)(msg)
        os.makedirs(BRIDGE_DIR, exist_ok=True)
        _write_json(os.path.join(BRIDGE_DIR, "heartbeat.json"), {"ts": now})
        _handle_request()
    except Exception:  # noqa: BLE001
        unreal.log_warning("[jokate] bridge tick error:\n" + traceback.format_exc())


def start():
    global _tick_handle
    stop()
    _tick_handle = unreal.register_slate_post_tick_callback(_tick)
    try:
        _register_menu()
    except Exception:  # noqa: BLE001
        unreal.log_warning("[jokate] 메뉴 등록 실패:\n" + traceback.format_exc())
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
