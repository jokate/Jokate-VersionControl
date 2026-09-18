"""
창 없는 단일 데몬: 한 프로세스에서 웹 UI(serve)와 자동 스냅샷(watch)을 같이 돌린다.

  python -m jokate daemon <project> [--port 8765]
  python -m jokate daemon-stop <project>

- HTTP 서버는 스레드, watch 는 poll_once 루프 스레드. paused 플래그로 일시정지 가능
- 상태 파일 <project>/.jokate/daemon.json {pid, port, started, tool_dir} — 시작 시 쓰고 종료 시 지움
- 이미 살아 있는 데몬(파일의 pid 생존 + 그 포트의 /api/info 응답)이면 '이미 실행 중' 출력 후 0
- 로그는 <project>/.jokate/daemon.log (pythonw 로 띄우면 stdout 이 없으므로 파일 로깅)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from . import uediff as uediffmod
from . import watch as watchmod
from . import web as webmod
from .config import Config
from .store import Store, format_ts
from .store import cleanup_tmp_files as store_cleanup_tmp_files

STATE_FILE = "daemon.json"
LOG_FILE = "daemon.log"
ALIVE_CODE = 259  # STILL_ACTIVE


# ---- 상태 파일 ----
def state_path(cfg: Config) -> Path:
    return cfg.state_dir / STATE_FILE


def log_path(cfg: Config) -> Path:
    return cfg.state_dir / LOG_FILE


def read_state(cfg: Config) -> dict | None:
    p = state_path(cfg)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  없거나 깨졌으면 없는 것으로
        return None


def write_state(cfg: Config, port: int, pid: int | None = None) -> dict:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    s = {"pid": int(pid if pid is not None else os.getpid()), "port": int(port),
         "started": format_ts(time.time()), "tool_dir": str(Path(__file__).resolve().parent.parent)}
    state_path(cfg).write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return s


def clear_state(cfg: Config) -> None:
    try:
        state_path(cfg).unlink()
    except OSError:
        pass


def write_log(cfg: Config, line: str) -> None:
    """파일에 기록하고, stdout 이 있으면 같이 출력 (pythonw 에서 sys.stdout 은 None)."""
    try:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        with log_path(cfg).open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        if sys.stdout is not None:
            print(line, flush=True)
    except Exception:  # noqa: BLE001
        pass


# ---- 살아 있는지 판정 ----
def pid_alive(pid: int | None) -> bool:
    if not pid or int(pid) <= 0:
        return False
    pid = int(pid)
    if os.name == "nt":
        import ctypes

        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == ALIVE_CODE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def ping(port: int | None, timeout: float = 1.0) -> bool:
    if not port:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/api/info", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def running_state(cfg: Config) -> dict | None:
    """살아 있는 데몬의 상태 파일 내용. 없거나 죽었으면 None."""
    s = read_state(cfg)
    if not s or not pid_alive(s.get("pid")) or not ping(s.get("port")):
        return None
    return s


# ---- 컨트롤 ----
class DaemonControl:
    """웹 핸들러가 부르는 데몬 조작 객체 (pause/resume/stop + 상태)."""

    def __init__(self, cfg: Config, port: int, pid: int | None = None) -> None:
        self.cfg = cfg
        self.port = int(port)
        self.pid = int(pid if pid is not None else os.getpid())
        self.started = format_ts(time.time())
        self.last_line = ""
        self._paused = threading.Event()
        self._stopping = threading.Event()
        self._snap_req = threading.Event()   # 트레이 '지금 스냅샷' → watch 루프가 집어간다

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def log(self, line: str) -> None:
        self.last_line = line
        write_log(self.cfg, line)

    def pause(self) -> dict:
        if not self._paused.is_set():
            self._paused.set()
            self.log(f"{format_ts(time.time())}  자동 스냅샷 일시정지")
        return self.status()

    def resume(self) -> dict:
        if self._paused.is_set():
            self._paused.clear()
            self.log(f"{format_ts(time.time())}  자동 스냅샷 재개")
        return self.status()

    def stop(self) -> dict:
        if not self._stopping.is_set():
            self._stopping.set()
            self.log(f"{format_ts(time.time())}  종료 요청")
        return self.status()

    def restart(self, launcher=None) -> dict:
        """새 데몬 프로세스를 띄우고 자신은 종료한다 (코드 업데이트 후 낡은 서버 교체용)."""
        pid = (launcher or spawn_daemon)(self.cfg, self.port)
        self.log(f"{format_ts(time.time())}  재시작: 새 데몬 pid={pid}")
        s = self.stop()
        s["restarted_pid"] = pid
        return s

    def snap_now(self) -> dict:
        """수동 스냅샷 요청. 실제 스냅은 watch 루프 스레드가 찍는다(sqlite 스레드 고정)."""
        self._snap_req.set()
        self.log(f"{format_ts(time.time())}  수동 스냅샷 요청")
        return self.status()

    def take_snap_request(self) -> bool:
        """요청이 있었으면 True 를 돌려주며 플래그를 내린다."""
        if self._snap_req.is_set():
            self._snap_req.clear()
            return True
        return False

    def wait_stop(self, timeout: float | None = None) -> bool:
        return self._stopping.wait(timeout)

    def status(self) -> dict:
        return {"running": True, "paused": self.paused, "stopping": self.stopping,
                "pid": self.pid, "port": self.port, "started": self.started,
                "last_line": self.last_line}


# ---- 정리(보관기간·GC) ----
RETENTION_INTERVAL = 24 * 3600      # 시작 시 한 번, 이후 24시간마다


def retention_once(cfg: Config, control: "DaemonControl") -> tuple[int, int, int]:
    """오래된 auto 스냅샷 정리 + 객체 GC. 지운 게 있으면 한 줄 로그. 반환 (스냅샷, 객체, 바이트)."""
    st = Store(cfg)
    try:
        ids = st.prune(auto_days=getattr(cfg, "auto_days", 14),
                       keep_last_auto=getattr(cfg, "keep_last_auto", 30), dry_run=True)
        if ids:
            st.delete_snapshots(ids)
        objects, size = st.gc()
    finally:
        st.close()
    if ids or objects:
        control.log(f"{format_ts(time.time())}  정리: 스냅샷 {len(ids)}개, "
                    f"객체 {objects}개 {size / 1048576:.1f} MB")
    return len(ids), objects, size


def retention_loop(cfg: Config, control: "DaemonControl", interval: float = RETENTION_INTERVAL) -> None:
    while not control.stopping:
        try:
            retention_once(cfg, control)
        except Exception as e:  # noqa: BLE001
            control.log(f"{format_ts(time.time())}  정리 오류: {type(e).__name__}: {e}")
        if control.wait_stop(interval):
            break


# ---- 의미 diff 사이드카 ----
def _capture_meta_async(cfg: Config, diff) -> threading.Thread:
    """스냅샷 직후 에디터에서 DataTable 내용을 받아 사이드카로 저장(폴링 루프를 막지 않게 워커 스레드)."""
    def work() -> None:
        from . import meta as metamod
        st = Store(cfg)
        try:
            metamod.capture_for_snapshot(st, diff)
        except Exception:  # noqa: BLE001  기록 실패는 스냅샷과 무관
            pass
        finally:
            st.close()

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


# ---- 루프 ----
def watch_loop(cfg: Config, control: DaemonControl, interval: float = 2.0, debounce: float = 5.0) -> None:
    st = Store(cfg)
    try:
        snap, d, _ = st.snap("")
        control.log(watchmod.format_line(watchmod.PollResult(snap, d)))
        if snap is not None:
            _capture_meta_async(cfg, d)
        state = watchmod.WatchState(watchmod.fingerprint(cfg), None)
        while not control.stopping:
            if control.wait_stop(interval):
                break
            if control.take_snap_request():      # 일시정지 중이어도 수동 요청은 찍는다
                try:
                    snap, d, _ = st.snap("트레이에서 수동 스냅샷")
                    control.log(watchmod.format_line(watchmod.PollResult(snap, d)))
                    if snap is not None:
                        _capture_meta_async(cfg, d)
                    state = watchmod.WatchState(watchmod.fingerprint(cfg), None)
                except Exception as e:  # noqa: BLE001
                    control.log(f"{format_ts(time.time())}  수동 스냅샷 실패: {type(e).__name__}: {e}")
            if control.paused:
                continue
            try:
                state, r = watchmod.poll_once(st, state, time.time(), debounce)
            except Exception as e:  # noqa: BLE001  한 번 실패해도 데몬은 계속
                control.log(f"{format_ts(time.time())}  watch 오류: {type(e).__name__}: {e}")
                continue
            if r is not None:
                control.log(watchmod.format_line(r))
                if r.snapshot is not None:
                    _capture_meta_async(cfg, r.diff)
    except Exception as e:  # noqa: BLE001
        control.log(f"{format_ts(time.time())}  watch 중단: {type(e).__name__}: {e}")
    finally:
        st.close()


# ---- 재시작 ----
WAIT_PORT_ENV = "JOKATE_WAIT_PORT"
TOOL_DIR = Path(__file__).resolve().parent.parent


def relaunch_command(cfg: Config) -> list[str]:
    """[pythonw 또는 python, '-m', 'jokate', 'daemon', <project>] — tool.json 의 인터프리터 우선."""
    exe = sys.executable
    tool = {}
    try:
        tool = json.loads((cfg.state_dir / "tool.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tool = {}
    exe = tool.get("pythonw") or tool.get("python") or str(Path(exe).with_name("pythonw.exe")
                                                          if Path(exe).with_name("pythonw.exe").exists() else exe)
    return [str(exe), "-m", "jokate", "daemon", str(cfg.root)]


def spawn_daemon(cfg: Config, port: int | None = None) -> int:
    """같은 인자로 새 데몬을 분리 실행 (PYTHON* 환경변수 제거, 포트가 빌 때까지 기다리게 표시)."""
    env = {k: v for k, v in os.environ.items() if not str(k).upper().startswith("PYTHON")}
    env[WAIT_PORT_ENV] = "1"
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED|NEW_GROUP|NO_WINDOW
    p = subprocess.Popen(  # noqa: S603
        relaunch_command(cfg), cwd=str(TOOL_DIR), env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, **kwargs)
    return p.pid


def wait_port_free(cfg: Config, timeout: float = 15.0, poll: float = 0.4) -> bool:
    """앞선 데몬이 물러날 때까지 대기 (JOKATE_WAIT_PORT=1 로 뜬 프로세스 전용)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if running_state(cfg) is None:
            return True
        time.sleep(poll)
    return running_state(cfg) is None


def run(cfg: Config, port: int | None = None, interval: float = 2.0, debounce: float = 5.0,
        *, host: str = "127.0.0.1") -> int:
    if os.environ.get(WAIT_PORT_ENV, "") == "1":
        wait_port_free(cfg)          # 재시작으로 뜬 프로세스: 이전 데몬이 포트를 놓을 때까지
    cur = running_state(cfg)
    if cur:
        write_log(cfg, f"{format_ts(time.time())}  이미 실행 중: pid {cur.get('pid')} port {cur.get('port')}")
        return 0

    control = DaemonControl(cfg, port if port is not None else cfg.port)
    httpd = ThreadingHTTPServer((host, control.port), webmod.make_handler(cfg, control))
    control.port = httpd.server_address[1]
    write_state(cfg, control.port, control.pid)
    control.log(f"{format_ts(time.time())}  daemon 시작  pid={control.pid} port={control.port} "
                f"http://{host}:{control.port}/")

    try:
        _st = Store(cfg)
        try:
            uediffmod.cleanup_tmp(_st)      # 지난 diff 임시 복사본 정리
            store_cleanup_tmp_files(cfg)    # 롤백이 남긴 오래된 *.jokate-tmp 정리
        finally:
            _st.close()
    except Exception:  # noqa: BLE001
        pass
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    t_watch = threading.Thread(target=watch_loop, args=(cfg, control, interval, debounce), daemon=True)
    t_watch.start()
    t_ret = threading.Thread(target=retention_loop, args=(cfg, control), daemon=True)
    t_ret.start()
    if getattr(cfg, "tray", True):
        from . import tray as traymod
        traymod.start(control, title=f"Jokate - {cfg.root.name}",
                      url=f"http://127.0.0.1:{control.port}/", log=control.log)
    try:
        while not control.wait_stop(0.5):
            pass
    except KeyboardInterrupt:
        control.stop()
    finally:
        time.sleep(0.2)          # 종료 응답을 보낼 여유
        httpd.shutdown()
        httpd.server_close()
        t_watch.join(timeout=3)
        clear_state(cfg)
        control.log(f"{format_ts(time.time())}  daemon 종료")
    return 0


def stop_remote(cfg: Config) -> int:
    """daemon.json 의 port 로 POST /api/daemon {action:stop}."""
    s = running_state(cfg)
    if not s:
        clear_state(cfg)
        print("실행 중인 데몬이 없습니다")
        return 0
    body = json.dumps({"action": "stop"}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{s['port']}/api/daemon", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        print(f"종료 요청 실패: {type(e).__name__}: {e}")
        return 1
    print(f"데몬 종료 요청함  pid {s.get('pid')} port {s.get('port')}")
    return 0
