"""트레이 메뉴 모델과 import 안전성 (실제 아이콘·창은 띄우지 않는다)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import tray  # noqa: E402  어떤 플랫폼에서도 import 자체는 예외 없이 되어야 한다


def test_menu_items_labels() -> None:
    running = tray.menu_items(False)
    paused = tray.menu_items(True)
    assert [i for i, _ in running] == [tray.ID_OPEN, tray.ID_SNAP, tray.ID_PAUSE, tray.ID_QUIT]
    assert [i for i, _ in running] == [i for i, _ in paused]
    labels = dict(running)
    assert labels[tray.ID_OPEN] == "타임라인 열기"
    assert labels[tray.ID_SNAP] == "지금 스냅샷"
    assert labels[tray.ID_PAUSE] == "자동 스냅샷 일시정지"
    assert labels[tray.ID_QUIT] == "종료"
    assert dict(paused)[tray.ID_PAUSE] == "자동 스냅샷 재개"


def test_start_is_best_effort(monkeypatch) -> None:
    """트레이를 못 쓰는 환경이면 예외 없이 로그 한 줄만 남기고 None. (실제 아이콘은 띄우지 않는다)"""
    lines: list[str] = []
    monkeypatch.setattr(tray, "_UNAVAILABLE", "테스트")
    assert tray.start(object(), title="Jokate - T", url="http://127.0.0.1:1/", log=lines.append) is None
    assert len(lines) == 1 and "트레이" in lines[0]


def test_control_snap_now(tmp_path: Path) -> None:
    from jokate import config as cfgmod
    from jokate import daemon as daemonmod

    root = tmp_path / "Proj"
    (root / "Content").mkdir(parents=True)
    cfgmod.init(root)
    c = daemonmod.DaemonControl(cfgmod.load(root), 1234)
    assert c.take_snap_request() is False
    c.snap_now()
    assert c.take_snap_request() is True
    assert c.take_snap_request() is False
