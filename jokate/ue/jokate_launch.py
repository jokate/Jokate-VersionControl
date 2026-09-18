"""
데몬 실행기 (UE 에디터 안팎 공용, 표준 라이브러리만: unreal 을 import 하지 않음)

- read_tool(project_dir)   : <project>/.jokate/tool.json {tool_dir, python, pythonw, autostart} 또는 None
- child_env(env)           : UE 가 심어 놓은 PYTHON* 환경변수를 지운 환경 사본
                             (이게 없으면 시스템 파이썬이 UE 의 파이썬 경로로 오염되어 즉사한다)
- build_command(tool, dir) : [pythonw 또는 python, '-m', 'jokate', 'daemon', <project>]
- launch(project_dir)      : 창 없이 떼어내서 실행하고 pid 반환
"""
import json
import os
import subprocess
import sys

TOOL_FILE = "tool.json"

# Windows 프로세스 생성 플래그 (비 Windows 에서는 쓰지 않는다)
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def tool_path(project_dir):
    return os.path.join(str(project_dir), ".jokate", TOOL_FILE)


def read_tool(project_dir):
    """tool.json 을 읽어 dict 로. 없거나 깨졌으면 None."""
    try:
        with open(tool_path(project_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def autostart_enabled(tool):
    """tool.json 의 autostart (기본 True) + 환경변수 JOKATE_NO_AUTOSTART=1 이면 False."""
    if os.environ.get("JOKATE_NO_AUTOSTART", "") == "1":
        return False
    if not tool:
        return True
    return bool(tool.get("autostart", True))


def child_env(env=None):
    """PYTHON 으로 시작하는 변수(PYTHONHOME/PYTHONPATH/PYTHONSTARTUP…)를 제거한 환경 사본."""
    src = os.environ if env is None else env
    return dict((k, v) for k, v in src.items() if not str(k).upper().startswith("PYTHON"))


def build_command(tool, project_dir):
    """pythonw 가 있으면 창 없는 pythonw, 없으면 python (그것도 없으면 현재 인터프리터)."""
    tool = tool or {}
    exe = tool.get("pythonw") or tool.get("python") or sys.executable
    return [str(exe), "-m", "jokate", "daemon", str(project_dir)]


def launch(project_dir):
    """데몬을 떼어내 실행하고 pid 반환. tool.json 이 없으면 RuntimeError."""
    tool = read_tool(project_dir)
    if tool is None:
        raise RuntimeError("%s 가 없습니다 — bridge-install 을 다시 실행하세요" % tool_path(project_dir))
    cwd = tool.get("tool_dir") or None
    if cwd and not os.path.isdir(str(cwd)):
        cwd = None
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    p = subprocess.Popen(  # noqa: S603
        build_command(tool, project_dir),
        cwd=cwd,
        env=child_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **kwargs
    )
    return p.pid
