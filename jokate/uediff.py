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
EXE_REL = "Engine/Binaries/Win64/UnrealEditor.exe"
HINT = ("UnrealEditor.exe 를 찾지 못했습니다. "
        ".jokate/config.toml 의 [editor] exe 에 UnrealEditor.exe 경로를 적으세요")


class EditorNotFound(RuntimeError):
    """언리얼 에디터 실행 파일을 못 찾음 (웹에서는 409)."""


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


def cleanup_tmp(store, max_age_hours: float = 24) -> int:
    """오래된 임시 추출 파일 삭제 → 지운 개수."""
    d = tmp_dir(store)
    if not d.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    n = 0
    for p in d.iterdir():
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError:
            pass
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


def open_diff(store, rel: str, a_sha: str, b_sha: str | None = None, launcher=None) -> dict:
    """왼쪽=a_sha 버전, 오른쪽=b_sha 버전(없으면 작업 트리 현재 파일). → {pid,left,right,cmd}"""
    rel = str(rel).replace("\\", "/").strip("/")
    if not rel:
        raise ValueError("rel 필요")
    cfg = store.cfg
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
    return {"pid": pid, "left": str(left).replace("\\", "/"),
            "right": str(right).replace("\\", "/"), "cmd": cmd}
