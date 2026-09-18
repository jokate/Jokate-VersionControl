"""데몬 실행기(jokate_launch): 환경 청소·명령 조립·tool.json 읽기, bridge.install 이 만드는 파일들."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jokate" / "ue"))

import jokate_launch as launch  # noqa: E402

from jokate import bridge  # noqa: E402
from jokate import config as cfgmod  # noqa: E402


def test_child_env_drops_python_vars() -> None:
    env = {"PYTHONHOME": "C:/UE/py", "PYTHONPATH": "C:/UE/lib", "PYTHONSTARTUP": "x.py",
           "pythonpath": "lower", "PATH": "C:/bin", "UE_PROJECT": "MNYS"}
    out = launch.child_env(env)
    assert out == {"PATH": "C:/bin", "UE_PROJECT": "MNYS"}
    assert env["PYTHONHOME"] == "C:/UE/py"  # 원본은 그대로


def test_build_command_prefers_pythonw() -> None:
    tool = {"python": "C:/Py/python.exe", "pythonw": "C:/Py/pythonw.exe"}
    assert launch.build_command(tool, "C:/Proj") == [
        "C:/Py/pythonw.exe", "-m", "jokate", "daemon", "C:/Proj"]
    assert launch.build_command({"python": "C:/Py/python.exe", "pythonw": None}, "C:/Proj")[0] == "C:/Py/python.exe"
    assert launch.build_command({}, "C:/Proj")[0] == sys.executable


def test_read_tool_missing_and_ok(tmp_path: Path) -> None:
    assert launch.read_tool(tmp_path) is None
    d = tmp_path / ".jokate"
    d.mkdir()
    (d / "tool.json").write_text("{ broken", encoding="utf-8")
    assert launch.read_tool(tmp_path) is None
    (d / "tool.json").write_text(json.dumps({"python": "p", "autostart": False}), encoding="utf-8")
    tool = launch.read_tool(tmp_path)
    assert tool["python"] == "p"
    assert launch.autostart_enabled(tool) is False
    assert launch.autostart_enabled({"autostart": True}) is True


def test_autostart_env_off(monkeypatch) -> None:
    monkeypatch.setenv("JOKATE_NO_AUTOSTART", "1")
    assert launch.autostart_enabled({"autostart": True}) is False


def test_install_writes_tool_json_and_launcher(tmp_path: Path) -> None:
    root = tmp_path / "Proj"
    (root / "Content").mkdir(parents=True)
    cfgmod.init(root)
    bridge.install(root)
    pydir = root / "Content" / "Python"
    assert (pydir / "jokate_launch.py").exists()
    assert (pydir / "jokate_bridge.py").exists()
    tool = json.loads((root / ".jokate" / "tool.json").read_text(encoding="utf-8"))
    assert Path(tool["tool_dir"]).name == "jokate" or (Path(tool["tool_dir"]) / "jokate").is_dir()
    assert tool["python"] == Path(sys.executable).resolve().as_posix()
    assert tool["autostart"] is True
    assert "\\" not in tool["tool_dir"] and "\\" not in tool["python"]
    assert launch.build_command(tool, root)[1:] == ["-m", "jokate", "daemon", str(root)]


def test_install_respects_autostart_false(tmp_path: Path) -> None:
    root = tmp_path / "Proj2"
    (root / "Content").mkdir(parents=True)
    cfgmod.init(root)
    (root / ".jokate" / "config.toml").write_text(
        '[project]\ncontent = "Content"\n\n[editor]\nautostart = false\n', encoding="utf-8")
    bridge.install(root)
    tool = json.loads((root / ".jokate" / "tool.json").read_text(encoding="utf-8"))
    assert tool["autostart"] is False
    assert launch.autostart_enabled(tool) is False
