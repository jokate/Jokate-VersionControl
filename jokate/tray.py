"""
Windows 트레이 아이콘 (ctypes 만, 외부 의존성 없음)

데몬이 시작할 때 자체 스레드에서 숨은 메시지 창 + Shell_NotifyIconW 로 아이콘을 띄운다.
우클릭 팝업 메뉴: 타임라인 열기 / 지금 스냅샷 / 일시정지·재개 / 종료, 더블클릭 = 타임라인 열기.
최선 노력: Windows 가 아니거나 어떤 예외든 나면 로그 한 줄 남기고 데몬은 그대로 계속 돈다.
메뉴 모델은 순수 함수 menu_items(paused) 로 분리(테스트 대상).
"""
from __future__ import annotations

import os
import threading
import webbrowser

ID_OPEN = 1001
ID_SNAP = 1002
ID_PAUSE = 1003
ID_QUIT = 1004


def menu_items(paused: bool) -> list[tuple[int, str]]:
    """(command_id, 라벨) 목록. 일시정지 항목 라벨은 현재 상태에 따라 바뀐다."""
    return [
        (ID_OPEN, "타임라인 열기"),
        (ID_SNAP, "지금 스냅샷"),
        (ID_PAUSE, "자동 스냅샷 재개" if paused else "자동 스냅샷 일시정지"),
        (ID_QUIT, "종료"),
    ]


_UNAVAILABLE: str | None = None
if os.name != "nt":
    _UNAVAILABLE = "Windows 가 아님"
else:
    try:  # pragma: no cover - Windows 전용
        import ctypes
        import ctypes.wintypes as wt

        WM_DESTROY = 0x0002
        WM_COMMAND = 0x0111
        WM_TRAY = 0x8000 + 1          # WM_APP + 1
        WM_LBUTTONDBLCLK = 0x0203
        WM_RBUTTONUP = 0x0205
        NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
        NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
        IDI_APPLICATION = 32512
        MF_STRING = 0x0000
        TPM_RETURNCMD = 0x0100
        TPM_RIGHTBUTTON = 0x0002

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                        ("hbrBackground", wt.HANDLE), ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR)]

        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
                        ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD)]
    except Exception as e:  # noqa: BLE001  ctypes.wintypes 가 없는 이상한 환경
        _UNAVAILABLE = "%s: %s" % (type(e).__name__, e)


class Tray:  # pragma: no cover - 실제 창/아이콘은 테스트에서 띄우지 않는다
    """숨은 메시지 창 + 트레이 아이콘. run() 은 자체 스레드에서 메시지 루프를 돈다."""

    def __init__(self, control, title: str, url: str) -> None:
        self.control = control
        self.title = title
        self.url = url
        self.hwnd = None
        self._nid = None
        self._proc = WNDPROC(self._wndproc)   # GC 방지
        self._u32 = ctypes.windll.user32
        self._shell = ctypes.windll.shell32

    # ---- 동작 ----
    def _open(self) -> None:
        webbrowser.open(self.url)

    def _invoke(self, cmd: int) -> None:
        if cmd == ID_OPEN:
            self._open()
        elif cmd == ID_SNAP:
            self.control.snap_now()
        elif cmd == ID_PAUSE:
            self.control.resume() if self.control.paused else self.control.pause()
        elif cmd == ID_QUIT:
            self.control.stop()

    def _popup(self) -> None:
        menu = self._u32.CreatePopupMenu()
        for cid, label in menu_items(self.control.paused):
            self._u32.AppendMenuW(menu, MF_STRING, cid, label)
        pt = wt.POINT()
        self._u32.GetCursorPos(ctypes.byref(pt))
        self._u32.SetForegroundWindow(self.hwnd)
        cmd = self._u32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                                       pt.x, pt.y, 0, self.hwnd, None)
        self._u32.PostMessageW(self.hwnd, 0, 0, 0)
        self._u32.DestroyMenu(menu)
        if cmd:
            self._invoke(int(cmd))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                low = int(lparam) & 0xFFFF
                if low == WM_RBUTTONUP:
                    self._popup()
                elif low == WM_LBUTTONDBLCLK:
                    self._open()
                return 0
            if msg == WM_COMMAND:
                self._invoke(int(wparam) & 0xFFFF)
                return 0
            if msg == WM_DESTROY:
                self._u32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001  콜백에서 예외가 새면 프로세스가 죽는다
            return 0
        return self._u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- 수명 ----
    def _create_window(self) -> None:
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc = WNDCLASS()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = "JokateTrayWnd_%d" % os.getpid()
        if not self._u32.RegisterClassW(ctypes.byref(wc)):
            raise OSError("RegisterClassW 실패")
        self._u32.CreateWindowExW.restype = wt.HWND
        self.hwnd = self._u32.CreateWindowExW(0, wc.lpszClassName, "Jokate", 0, 0, 0, 0, 0,
                                              None, None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW 실패")

    def _add_icon(self) -> None:
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._u32.LoadIconW(None, wt.LPCWSTR(IDI_APPLICATION))
        nid.szTip = self.title[:127]
        self._nid = nid
        self._shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def remove(self) -> None:
        if self._nid is not None:
            try:
                self._shell.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:  # noqa: BLE001
                pass
            self._nid = None

    def run(self) -> None:
        self._create_window()
        self._add_icon()
        msg = wt.MSG()
        try:
            while not self.control.stopping:
                if self._u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                    if msg.message == 0x0012:  # WM_QUIT
                        break
                    self._u32.TranslateMessage(ctypes.byref(msg))
                    self._u32.DispatchMessageW(ctypes.byref(msg))
                else:
                    self.control.wait_stop(0.1)
        finally:
            self.remove()
            if self.hwnd:
                try:
                    self._u32.DestroyWindow(self.hwnd)
                except Exception:  # noqa: BLE001
                    pass


def start(control, title: str, url: str, log=None) -> threading.Thread | None:
    """트레이 아이콘 스레드를 띄운다. 실패해도 예외를 밖으로 내지 않고 None 을 돌려준다."""
    def note(line: str) -> None:
        if log is not None:
            try:
                log(line)
            except Exception:  # noqa: BLE001
                pass

    if _UNAVAILABLE:
        note("트레이 아이콘 생략 (%s)" % _UNAVAILABLE)
        return None

    def work() -> None:
        try:
            Tray(control, title, url).run()
        except Exception as e:  # noqa: BLE001
            note("트레이 아이콘 실패: %s: %s" % (type(e).__name__, e))

    try:
        t = threading.Thread(target=work, name="jokate-tray", daemon=True)
        t.start()
        return t
    except Exception as e:  # noqa: BLE001
        note("트레이 아이콘 실패: %s: %s" % (type(e).__name__, e))
        return None
