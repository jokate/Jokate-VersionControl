"""
UE 에디터 브릿지 (도구 쪽)

파일 기반 프로토콜, 외부 의존성 없음. 디렉터리 <project>/.jokate/bridge/ 에
- heartbeat.json      : 에디터 쪽이 1초마다 {"ts": epoch} 갱신
- request.json        : 도구가 쓰는 요청 {"id", "op", "packages": ["/Game/..."], "args": {...}}
- response-<id>.json  : 에디터 쪽 응답 {"id", "ok", ...}

에디터 쪽 스크립트는 jokate/ue/jokate_bridge.py (bridge-install 로 <project>/Content/Python/ 에 복사).
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from .config import Config

HEARTBEAT_MAX_AGE = 3.0
_SRC = Path(__file__).resolve().parent / "ue" / "jokate_bridge.py"
INIT_LINE = "import jokate_bridge"


def bridge_dir(cfg: Config) -> Path:
    return cfg.state_dir / "bridge"


def heartbeat_age(cfg: Config) -> float | None:
    """heartbeat.json 의 ts 로부터 지난 초. 파일이 없거나 깨졌으면 None."""
    p = bridge_dir(cfg) / "heartbeat.json"
    try:
        ts = float(json.loads(p.read_text(encoding="utf-8"))["ts"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return max(0.0, time.time() - ts)


def bridge_alive(cfg: Config, max_age: float = HEARTBEAT_MAX_AGE) -> bool:
    age = heartbeat_age(cfg)
    return age is not None and age <= max_age


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def request(cfg: Config, op: str, packages: list[str], args: dict | None = None,
            timeout: float = 30.0, poll: float = 0.2) -> dict:
    """요청을 쓰고 응답을 기다린다. 응답 dict 반환, 시간 초과면 TimeoutError."""
    d = bridge_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    rid = uuid.uuid4().hex[:12]
    req = d / "request.json"
    resp = d / f"response-{rid}.json"
    _write_json(req, {"id": rid, "op": op, "packages": list(packages), "args": args or {}})
    deadline = time.time() + timeout
    while time.time() < deadline:
        if resp.exists():
            try:
                data = json.loads(resp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(poll)      # 아직 쓰는 중
                continue
            try:
                resp.unlink()
            except OSError:
                pass
            return data
        time.sleep(poll)
    try:
        if req.exists() and json.loads(req.read_text(encoding="utf-8")).get("id") == rid:
            req.unlink()
    except (OSError, ValueError):
        pass
    raise TimeoutError(f"에디터 브릿지 응답 없음 ({op}, {timeout:.0f}s)")


def install(project: Path) -> list[str]:
    """jokate_bridge.py 를 <project>/Content/Python/ 에 복사하고 init_unreal.py 에 import 줄을 보장. 한 일 목록 반환."""
    done: list[str] = []
    pydir = Path(project).resolve() / "Content" / "Python"
    pydir.mkdir(parents=True, exist_ok=True)
    dst = pydir / "jokate_bridge.py"
    if not dst.exists() or dst.read_bytes() != _SRC.read_bytes():
        shutil.copyfile(_SRC, dst)
        done.append(f"copied {dst}")
    init = pydir / "init_unreal.py"
    if not init.exists():
        init.write_text(INIT_LINE + "\n", encoding="utf-8")
        done.append(f"created {init}")
    else:
        lines = [ln.strip() for ln in init.read_text(encoding="utf-8").splitlines()]
        if INIT_LINE not in lines:
            with init.open("a", encoding="utf-8") as f:
                f.write(("" if init.stat().st_size == 0 or init.read_text(encoding="utf-8").endswith("\n") else os.linesep)
                        + INIT_LINE + "\n")
            done.append(f"appended '{INIT_LINE}' to {init}")
    return done
