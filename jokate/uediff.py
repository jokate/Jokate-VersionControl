"""애셋 두 버전을 언리얼 에디터의 diff 창으로 열기 (표준 라이브러리만).

  UnrealEditor.exe <프로젝트.uproject> -diff <왼쪽> <오른쪽>

에디터 경로는 ① config.toml [editor] exe ② .uproject 의 EngineAssociation(버전 → HKLM,
GUID(로컬 빌드) → HKCU) 순으로 찾는다. 비교할 버전은 store 객체를 .jokate/tmp/diff 로
원래 확장자를 붙여 복사해서 넘긴다(UE 는 확장자 없는 파일을 못 읽는다).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

TMP_SUBDIR = "tmp/diff"
EDITOR_TMP_REL = "Saved/JokateDiff"        # 전략 1: 파일 경로로 직접 로드
CONTENT_DIFF_DIR = "_JokateDiff"           # 전략 2: Content/_JokateDiff/<sha8>/<이름> (= /Game/_JokateDiff/...)
DIFF_TIMEOUT = 60.0
EXE_REL = "Engine/Binaries/Win64/UnrealEditor.exe"
HINT = ("UnrealEditor.exe 를 찾지 못했습니다. "
        ".jokate/config.toml 의 [editor] exe 에 UnrealEditor.exe 경로를 적으세요")


BRIDGE_OFF_HINT = ("에디터는 켜져 있는데 브릿지가 꺼져 있습니다 — 에디터 상단 툴 메뉴의 "
                   "Jokate > 브릿지 켜기 를 누르거나 에디터를 다시 시작하세요")


class EditorNotFound(RuntimeError):
    """언리얼 에디터 실행 파일을 못 찾음 (웹에서는 409)."""


class DiffBlocked(RuntimeError):
    """에디터가 켜져 있어 두 번째 에디터를 띄우지 않고 막음 (웹에서는 409)."""


def find_uproject(root: str | Path) -> Path | None:
    """프로젝트 폴더의 *.uproject 하나 (여러 개면 이름순 첫 번째)."""
    found = sorted(Path(root).glob("*.uproject"))
    return found[0] if found else None


def engine_association(uproject: str | Path) -> str:
    """*.uproject 의 EngineAssociation ('5.7' 또는 '{GUID}')."""
    try:
        data = json.loads(Path(uproject).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    return str(data.get("EngineAssociation", "") or "")


def _winreg_read(hive: str, key: str, name: str) -> str | None:
    """레지스트리 문자열 값 하나. 윈도우가 아니거나 없으면 None."""
    if sys.platform != "win32":
        return None
    import winreg  # 윈도우 전용 — 함수 안에서 import
    root = winreg.HKEY_LOCAL_MACHINE if hive == "HKLM" else winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(root, key) as k:
            val, _ = winreg.QueryValueEx(k, name)
        return str(val)
    except OSError:
        return None


def engine_dir(assoc: str, reg_read=None) -> Path | None:
    """EngineAssociation → 엔진 설치 폴더. reg_read(hive, key, name) 주입 가능."""
    assoc = (assoc or "").strip()
    if not assoc:
        return None
    read = reg_read or _winreg_read
    if assoc[0].isdigit():   # '5.7' 같은 설치 버전
        d = read("HKLM", r"SOFTWARE\EpicGames\Unreal Engine\%s" % assoc, "InstalledDirectory")
    else:                    # '{GUID}' — 소스 빌드 등록
        d = read("HKCU", r"Software\Epic Games\Unreal Engine\Builds", assoc)
    return Path(str(d).replace("\\", "/")) if d else None


def find_editor_exe(cfg, reg_read=None) -> Path | None:
    """UnrealEditor.exe 경로. config → EngineAssociation 순, 실제로 있어야 반환."""
    exe = str(getattr(cfg, "editor_exe", "") or "").strip()
    if exe:
        p = Path(exe.replace("\\", "/"))
        return p if p.is_file() else None
    up = find_uproject(cfg.root)
    if up is None:
        return None
    d = engine_dir(engine_association(up), reg_read)
    if d is None:
        return None
    p = d / EXE_REL
    return p if p.is_file() else None


def tmp_dir(store) -> Path:
    return store.cfg.state_dir / TMP_SUBDIR


def extract_version(store, rel: str, sha: str) -> Path:
    """store 객체를 .jokate/tmp/diff/<이름>__<sha8>.<확장자> 로 꺼낸다(이미 있으면 재사용)."""
    rel = str(rel).replace("\\", "/").strip("/")
    sha = str(sha or "").strip().lower()
    if not sha or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("sha 는 16진수만")
    src = store.object_path(sha)
    if not src.exists():
        raise KeyError(f"객체 없음: {sha[:8]}")
    name = Path(rel).name or "asset"
    stem, ext = os.path.splitext(name)
    out = tmp_dir(store) / f"{stem}__{sha[:8]}{ext}"
    if out.exists() and out.stat().st_size == src.stat().st_size:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, out)
    return out


def editor_tmp_dir(cfg) -> Path:
    """<project>/Saved/JokateDiff — 에디터가 파일 경로로 직접 로드할 버전 복사본."""
    return cfg.root / EDITOR_TMP_REL


def content_diff_dir(cfg) -> Path:
    """<project>/Content/_JokateDiff — /Game/_JokateDiff 로 로드할 버전 복사본."""
    return cfg.content / CONTENT_DIFF_DIR


def extract_for_editor(store, rel: str, sha: str) -> dict:
    """버전을 두 곳(Saved/JokateDiff, Content/_JokateDiff)에 '원래 이름 그대로' 꺼낸다.

    이름을 바꾸면 패키지 안의 애셋 이름과 어긋나 에디터가 로드하지 못한다.
    → {file, content_file, package}
    """
    rel = str(rel).replace("\\", "/").strip("/")
    sha = str(sha or "").strip().lower()
    if not sha or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("sha 는 16진수만")
    src = store.object_path(sha)
    if not src.exists():
        raise KeyError(f"객체 없음: {sha[:8]}")
    cfg = store.cfg
    name = Path(rel).name or "asset"
    stem = os.path.splitext(name)[0]
    sub = sha[:8]
    out = []
    for base in (editor_tmp_dir(cfg), content_diff_dir(cfg)):
        p = base / sub / name
        if not (p.exists() and p.stat().st_size == src.stat().st_size):
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, p)
        out.append(p)
    return {"file": out[0].as_posix(), "content_file": out[1].as_posix(),
            "package": f"/Game/{CONTENT_DIFF_DIR}/{sub}/{stem}"}


def extract_to_saved(store, rel: str, sha: str) -> Path:
    """버전을 <project>/Saved/JokateDiff/<sha8>/<원래 이름> 으로 꺼낸다(이미 있으면 재사용).

    UE 리비전 컨트롤 프로바이더의 내장 diff 가 파일 경로로 직접 읽는다.
    sha 검증·객체 확인은 extract_version 과 같다 (ValueError / KeyError).
    """
    src = extract_version(store, rel, sha)          # sha 검증 + 객체 존재 확인
    name = Path(str(rel).replace("\\", "/").strip("/")).name or "asset"
    out = editor_tmp_dir(store.cfg) / str(sha).strip().lower()[:8] / name
    if out.exists() and out.stat().st_size == src.stat().st_size:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, out)
    return out


def _cleanup_dirs(root: Path, cutoff: float) -> int:
    """<root>/<sha8>/ 중 오래된 폴더 삭제 → 지운 개수 (에디터가 잡고 있으면 조용히 건너뜀)."""
    n = 0
    if not root.is_dir():
        return 0
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d)
                n += 1
        except OSError:
            pass
    return n


def cleanup_tmp(store, max_age_hours: float = 24) -> int:
    """오래된 임시 추출물 삭제 → 지운 개수 (.jokate/tmp/diff 파일 + 에디터용 두 폴더의 하위 폴더)."""
    cutoff = time.time() - max_age_hours * 3600
    n = 0
    d = tmp_dir(store)
    if d.exists():
        for p in d.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    n += 1
            except OSError:
                pass
    cfg = store.cfg
    n += _cleanup_dirs(editor_tmp_dir(cfg), cutoff)
    n += _cleanup_dirs(content_diff_dir(cfg), cutoff)
    return n


def build_command(exe: str | Path, uproject: str | Path, left: str | Path, right: str | Path) -> list[str]:
    return [str(exe), str(uproject), "-diff", str(left), str(right)]


def launch(cmd: list[str]) -> int:
    """창을 분리해서 띄우고 pid. UE 가 심은 PYTHON* 환경변수는 지운다."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    p = subprocess.Popen(cmd, env=env, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    return p.pid


def _editor_is_running(cfg=None) -> bool:
    from .store import editor_running
    return editor_running(cfg)


def open_in_editor(store, rel: str, a_sha: str, b_sha: str | None = None, bridge=None) -> dict:
    """이미 켜져 있는 에디터에게 브릿지 op 'diff' 를 시켜 diff 창을 연다. → {mode:'editor', strategy}"""
    if bridge is None:
        from . import bridge as bridge  # noqa: PLW0127
    cfg = store.cfg
    left = extract_for_editor(store, rel, a_sha)
    if b_sha:
        right = extract_for_editor(store, rel, b_sha)
        right_file, right_package = right["file"], right["package"]
        right_label = str(b_sha)[:8]
    else:
        cur = cfg.content / rel
        if not cur.is_file():
            raise ValueError(f"작업 트리에 현재 파일이 없습니다: {rel}")
        right_file = cur.as_posix()
        right_package = "/Game/" + os.path.splitext(rel)[0]
        right_label = "현재"
    args = {"rel": rel,
            "left_file": left["file"], "left_package": left["package"],
            "right_file": right_file, "right_package": right_package,
            "left_label": str(a_sha)[:8], "right_label": right_label}
    resp = bridge.request(cfg, "diff", [], args, timeout=DIFF_TIMEOUT)
    if not resp.get("ok"):
        err = str(resp.get("error") or "에디터 diff 실패")
        tried = str(resp.get("strategy") or "").strip() or "없음"
        raise DiffBlocked(f"에디터에서 diff 를 열지 못했습니다: {err} (시도한 전략: {tried})")
    return {"mode": "editor", "strategy": resp.get("strategy"), "pid": None,
            "left": left["file"], "right": right_file, "note": ""}


def plan_diff(store, bridge=None, editor_running=None) -> dict:
    """요청 전에 어떤 방식이 될지 미리 알려준다 → {mode, editor_running, bridge, hint}.

    mode='editor' 면 켜져 있는 에디터에서 연다(브릿지가 꺼져 있으면 hint 가 막힐 이유),
    mode='process' 면 새 에디터 프로세스를 띄운다(1분쯤 걸림) — 확인 모달을 먼저 띄우라는 뜻.
    """
    cfg = store.cfg
    if bridge is None:
        from . import bridge as bridge  # noqa: PLW0127
    running = editor_running or _editor_is_running
    try:
        is_running = bool(running(cfg))
    except Exception:  # noqa: BLE001
        is_running = False
    alive = False
    if is_running:
        try:
            alive = bool(bridge.bridge_alive(cfg))
        except Exception:  # noqa: BLE001
            alive = False
    if is_running and not alive:
        hint = BRIDGE_OFF_HINT
    elif is_running:
        hint = ""
    else:
        hint = "에디터가 꺼져 있습니다. diff 를 보려면 에디터를 새로 띄워야 하며 1분쯤 걸립니다. 띄울까요?"
    return {"mode": "editor" if is_running else "process",
            "editor_running": is_running, "bridge": alive, "hint": hint}


def open_diff(store, rel: str, a_sha: str, b_sha: str | None = None, launcher=None,
              bridge=None, editor_running=None) -> dict:
    """왼쪽=a_sha, 오른쪽=b_sha(없으면 현재 파일).

    에디터가 켜져 있으면 무조건 그 에디터 안에서 연다(mode='editor'). 브릿지가 꺼져 있거나
    브릿지 op 가 실패하면 DiffBlocked — 두 번째 에디터는 절대 띄우지 않는다.
    에디터가 꺼져 있을 때만 새 에디터 프로세스를 띄운다(mode='process').
    """
    rel = str(rel).replace("\\", "/").strip("/")
    if not rel:
        raise ValueError("rel 필요")
    cfg = store.cfg
    if bridge is None:
        from . import bridge as bridge  # noqa: PLW0127
    running = editor_running or _editor_is_running
    try:
        is_running = bool(running(cfg))
    except Exception as e:  # noqa: BLE001
        is_running, note = False, f"에디터 상태 확인 실패: {type(e).__name__}: {e}"
    else:
        note = ""
    if is_running:
        try:
            alive = bool(bridge.bridge_alive(cfg))
        except Exception:  # noqa: BLE001
            alive = False
        if not alive:
            raise DiffBlocked(BRIDGE_OFF_HINT)
        try:
            r = open_in_editor(store, rel, a_sha, b_sha, bridge=bridge)
        except (KeyError, ValueError, DiffBlocked):
            raise
        except Exception as e:  # noqa: BLE001
            raise DiffBlocked(f"에디터에서 diff 를 열지 못했습니다: {type(e).__name__}: {e}") from e
        cleanup_tmp(store)
        return r
    if not note:
        note = "에디터가 꺼져 있어 새 에디터로 엽니다"
    exe = find_editor_exe(cfg)
    if exe is None:
        raise EditorNotFound(HINT)
    up = find_uproject(cfg.root)
    if up is None:
        raise EditorNotFound(f"{cfg.root} 에서 .uproject 를 찾지 못했습니다")
    left = extract_version(store, rel, a_sha)
    if b_sha:
        right = extract_version(store, rel, b_sha)
    else:
        cur = cfg.content / rel
        if not cur.is_file():
            raise ValueError(f"작업 트리에 현재 파일이 없습니다: {rel}")
        right = cur
    cleanup_tmp(store)
    cmd = build_command(exe, up, left, right)
    pid = (launcher or launch)(cmd)
    return {"mode": "process", "strategy": None, "note": note, "pid": pid,
            "left": str(left).replace("\\", "/"),
            "right": str(right).replace("\\", "/"), "cmd": cmd}
