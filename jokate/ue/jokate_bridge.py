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
물어보므로 게임 스레드에서 동기로 부르면 데드락. 결과는 _RESULTS 큐 → _tick 에서 unreal.log + 모달 창.
되돌리기 흐름: 메뉴 → (워커) head_id + restore_preview → (틱) 드라이런 확인창 YES/NO → (워커) restore POST
→ (틱) 완료 창 / 409 면 '저장 안 한 변경을 버릴까요?' 확인 후 discard_dirty 재요청. 모달은 틱에서만 띄운다.
"""
import json
import os
import queue
import threading
import time
import traceback

import unreal

def _sibling(modname):
    """같은 폴더의 모듈을 import (Content/Python 에 함께 설치되지만 저장소에서 바로 로드할 수도 있다)."""
    try:
        return __import__(modname)
    except ImportError:
        import importlib.util as _ilu
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), modname + ".py")
        spec = _ilu.spec_from_file_location(modname, path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


_client = _sibling("jokate_client")
_launch = _sibling("jokate_launch")

POLL_INTERVAL = 1.0

_project_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
CONTENT_DIR = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())
BRIDGE_DIR = os.path.join(_project_dir, ".jokate", "bridge")
_last_poll = 0.0
_tick_handle = None
_RESULTS = queue.Queue()  # (kind, payload) — 워커 스레드가 넣고 _tick(게임 스레드)이 로그/모달 처리
_menu_registered = False
_dialog_open = False  # 모달이 떠 있는 동안 큐 처리 재진입 금지
SERVE_HINT = "먼저 python -m jokate serve <프로젝트> 를 실행하세요"
DEAD_DAEMON = "데몬이 꺼져 있습니다 — start.bat 을 실행하거나 에디터를 다시 시작하세요"
RESTORE_TITLE = "Jokate 되돌리기"


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


def _alert(title, msg, warn=True):
    """게임 스레드에서 OK 창 + 로그."""
    _RESULTS.put(("alert", {"title": title, "text": msg, "warn": warn}))


def _run_bg(fn, *args):
    def work():
        try:
            fn(*args)
        except _client.ConnectionError as e:
            _alert("Jokate", "%s\n(%s)" % (DEAD_DAEMON, e))
        except Exception:  # noqa: BLE001
            _report("warn", "[jokate] 오류:\n" + traceback.format_exc())
    threading.Thread(target=work, daemon=True).start()


def _msg_box(title, text, yes_no=False):
    """모달 창 — 반드시 게임 스레드(_tick)에서만 호출."""
    kind = unreal.AppMsgType.YES_NO if yes_no else unreal.AppMsgType.OK
    ret = unreal.EditorDialog.show_message(title, text, kind)
    return bool(yes_no and ret == unreal.AppReturnType.YES)


def _do_snap(base, names, rels):
    message = "에디터에서 올림: %s %d개" % (", ".join(names[:5]) + (" …" if len(names) > 5 else ""), len(names))
    code, j = _client.snap_only(base, message, rels)
    if code == 200 and j.get("snapshot"):
        _report("log", "[jokate] 스냅샷 #%s 생성 (%d개)" % (j["snapshot"]["id"], len(rels)))
    elif code == 200:
        _alert("Jokate 올리기", "변경 없음 — 스냅샷을 만들지 않았습니다.")
    else:
        _alert("Jokate 올리기", "올리기 실패 %s: %s" % (code, j.get("error", j)))


def _do_preview(base, rels):
    """워커 스레드: HEAD + 드라이런 → 확인창 요청을 큐에 넣는다 (적용하지 않음)."""
    sid = _client.head_id(base)
    if sid is None:
        _alert(RESTORE_TITLE, "스냅샷이 없어 되돌릴 수 없습니다.")
        return
    code, j = _client.restore_preview(base, sid, rels)
    if code != 200:
        _alert(RESTORE_TITLE, "되돌리기 미리보기 실패 %s: %s" % (code, (j or {}).get("error", j)))
        return
    _RESULTS.put(("confirm_restore", {"base": base, "sid": sid, "rels": rels, "preview": j}))


def _do_restore(base, sid, rels, discard_dirty=False):
    """워커 스레드: 실제 적용."""
    code, j = _client.restore_assets(base, sid, rels, discard_dirty=discard_dirty)
    j = j or {}
    if code == 200 and j.get("ok"):
        _RESULTS.put(("restore_done", {"text": _client.format_result(j)}))
    elif code == 409 and j.get("dirty") and not discard_dirty:
        _RESULTS.put(("confirm_dirty", {"base": base, "sid": sid, "rels": rels, "body": j}))
    elif code == 409:
        _alert(RESTORE_TITLE, _client.format_blocked(j))
    else:
        _alert(RESTORE_TITLE, "되돌리기 실패 %s: %s" % (code, j.get("error", j)))


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
        unreal.log("[jokate] 되돌리기 미리보기를 불러오는 중… (%d개)" % len(rels))
        _run_bg(_do_preview, base, rels)
    elif kind == "history":
        _open_web(asset_rel=rels[0])


MENU_ITEMS = [
    ("snap", "선택한 애셋 올리기(스냅샷)", "선택한 애셋만 부분 스냅샷으로 올립니다"),
    ("restore", "선택한 애셋을 마지막 스냅샷 상태로 되돌리기", "무엇이 바뀌는지 확인창을 먼저 띄웁니다"),
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


def _table_from_json_string(dt):
    """DataTableFunctionLibrary.export_data_table_to_json_string 경로."""
    text = unreal.DataTableFunctionLibrary.export_data_table_to_json_string(dt)
    data = json.loads(text)
    rows = {}
    columns = []
    if isinstance(data, dict):
        items = [dict(v, Name=k) if isinstance(v, dict) else {"Name": k} for k, v in data.items()]
    else:
        items = list(data or [])
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or item.get("name") or len(rows))
        cells = {}
        for k, v in item.items():
            if k in ("Name", "name"):
                continue
            k = str(k)
            if k not in columns:
                columns.append(k)
            cells[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        rows[name] = cells
    return columns, rows


def _table_from_columns(dt):
    """get_data_table_row_names + 열 이름 + get_data_table_column_as_string 경로."""
    names = [str(n) for n in unreal.DataTableFunctionLibrary.get_data_table_row_names(dt)]
    columns = []
    for attr in ("get_data_table_column_names", "get_data_table_column_export_names"):
        if hasattr(unreal.DataTableFunctionLibrary, attr):
            try:
                columns = [str(c) for c in getattr(unreal.DataTableFunctionLibrary, attr)(dt)]
            except Exception:  # noqa: BLE001
                columns = []
            if columns:
                break
    rows = dict((n, {}) for n in names)
    for col in columns:
        try:
            vals = list(unreal.DataTableFunctionLibrary.get_data_table_column_as_string(dt, col))
        except Exception:  # noqa: BLE001
            continue
        for i, n in enumerate(names):
            rows[n][col] = str(vals[i]) if i < len(vals) else ""
    return columns, rows


def _op_export_meta(packages, args):
    """DataTable 내용을 JSON 으로 뽑는다. dirty(저장 안 됨)면 건너뛴다."""
    dirty = set(_dirty_names(packages))
    meta = {}
    skipped = []
    errors = {}
    for name in packages:
        if name in dirty:
            skipped.append(name)
            continue
        try:
            asset = unreal.EditorAssetLibrary.load_asset(name)
        except Exception as e:  # noqa: BLE001
            errors[name] = "load 실패: %s" % e
            continue
        if asset is None or not isinstance(asset, unreal.DataTable):
            skipped.append(name)
            continue
        row_struct = ""
        try:
            rs = asset.get_editor_property("row_struct")
            row_struct = rs.get_name() if rs is not None else ""
        except Exception:  # noqa: BLE001
            row_struct = ""
        columns, rows, err = [], {}, None
        try:
            if hasattr(unreal, "DataTableFunctionLibrary") and hasattr(
                    unreal.DataTableFunctionLibrary, "export_data_table_to_json_string"):
                columns, rows = _table_from_json_string(asset)
            elif hasattr(unreal, "DataTableFunctionLibrary") and hasattr(
                    unreal.DataTableFunctionLibrary, "get_data_table_row_names"):
                columns, rows = _table_from_columns(asset)
            else:
                err = "이 엔진 버전에는 DataTable 읽기 API 가 없음"
        except Exception as e:  # noqa: BLE001
            err = "%s: %s" % (type(e).__name__, e)
        if err:
            errors[name] = err
            continue
        meta[name] = {"kind": "DataTable", "row_struct": row_struct,
                      "columns": columns, "rows": rows}
    return {"ok": True, "meta": meta, "skipped": skipped, "errors": errors}


_OPS = {"dirty": _op_dirty, "reload": _op_reload, "export_meta": _op_export_meta}


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


def _handle_result(kind, payload):
    """게임 스레드에서 워커 결과 하나를 처리 (모달은 여기서만 띄운다)."""
    if kind == "alert":
        text = payload["text"]
        (unreal.log_warning if payload.get("warn", True) else unreal.log)("[jokate] " + text)
        _msg_box(payload.get("title") or "Jokate", text)
    elif kind == "confirm_restore":
        preview, sid = payload["preview"], payload["sid"]
        if _client.preview_change_count(preview) == 0:
            unreal.log("[jokate] 이미 마지막 스냅샷 상태")
            _msg_box(RESTORE_TITLE, "이미 마지막 스냅샷 상태입니다 — 되돌릴 변경이 없습니다.")
            return
        if _msg_box(RESTORE_TITLE, _client.format_preview(preview, sid), yes_no=True):
            unreal.log("[jokate] 되돌리는 중… (%d개)" % len(payload["rels"]))
            _run_bg(_do_restore, payload["base"], sid, payload["rels"], False)
        else:
            unreal.log("[jokate] 되돌리기 취소")
    elif kind == "confirm_dirty":
        body = payload["body"]
        unreal.log_warning("[jokate] " + _client.format_blocked(body))
        text = _client.format_blocked(body) + "\n\n저장하지 않은 변경을 버리고 진행할까요?"
        if _msg_box(RESTORE_TITLE, text, yes_no=True):
            _run_bg(_do_restore, payload["base"], payload["sid"], payload["rels"], True)
        else:
            unreal.log("[jokate] 되돌리기 취소 — 저장 후 다시 시도하세요")
    elif kind == "restore_done":
        unreal.log("[jokate] " + payload["text"])
        _msg_box(RESTORE_TITLE, payload["text"])
    else:  # 'log' / 'warn' 문자열 메시지
        (unreal.log_warning if kind == "warn" else unreal.log)(payload)


def _drain_results():
    global _dialog_open
    if _dialog_open:  # 모달이 떠 있는 동안 재진입 금지
        return
    _dialog_open = True
    try:
        while True:
            try:
                kind, payload = _RESULTS.get_nowait()
            except queue.Empty:
                break
            try:
                _handle_result(kind, payload)
            except Exception:  # noqa: BLE001
                unreal.log_warning("[jokate] 결과 처리 오류:\n" + traceback.format_exc())
    finally:
        _dialog_open = False


def _tick(delta_seconds):
    global _last_poll
    now = time.time()
    if now - _last_poll < POLL_INTERVAL:
        return
    _last_poll = now
    try:
        _drain_results()
        os.makedirs(BRIDGE_DIR, exist_ok=True)
        _write_json(os.path.join(BRIDGE_DIR, "heartbeat.json"), {"ts": now})
        _handle_request()
    except Exception:  # noqa: BLE001
        unreal.log_warning("[jokate] bridge tick error:\n" + traceback.format_exc())


def _autostart_check():
    """데몬이 떠 있는지 2초 안에 확인하고, 없으면 창 없이 띄운다 (워커 스레드 전용 — 게임 스레드에서 부르지 말 것)."""
    tool = _launch.read_tool(_project_dir)
    if tool is None:
        _report("warn", "[jokate] .jokate/tool.json 이 없어 데몬 자동 실행 생략 — bridge-install 을 다시 실행하세요")
        return
    if not _launch.autostart_enabled(tool):
        return
    base = _client.base_url(_project_dir)
    try:
        code, j = _client.daemon_status(base, 2.0)
    except _client.ConnectionError:
        pid = _launch.launch(_project_dir)
        _report("log", "[jokate] 데몬 자동 실행 pid=%s" % pid)
        return
    if code == 200 and isinstance(j, dict) and not j.get("running"):
        _report("warn", "[jokate] 포트를 serve 단독 서버가 쓰는 중 — 자동 스냅샷이 꺼져 있다")


def start():
    global _tick_handle
    stop()
    _tick_handle = unreal.register_slate_post_tick_callback(_tick)
    try:
        _register_menu()
    except Exception:  # noqa: BLE001
        unreal.log_warning("[jokate] 메뉴 등록 실패:\n" + traceback.format_exc())
    _run_bg(_autostart_check)
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
