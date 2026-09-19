"""22b: plugin-install (에디터 플러그인 복사)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import plugin as pluginmod  # noqa: E402


def test_source_dir_has_plugin():
    src = pluginmod.source_dir()
    assert (src / pluginmod.UPLUGIN).is_file()
    assert (src / "Source" / "JokateSourceControl" / "JokateSourceControl.Build.cs").is_file()


def test_install_copies_source_and_uplugin(tmp_path: Path):
    proj = tmp_path / "Proj"
    proj.mkdir()
    res = pluginmod.install(proj)
    dest = proj / "Plugins" / "JokateSourceControl"
    assert (dest / pluginmod.UPLUGIN).is_file()
    assert (dest / "Source" / "JokateSourceControl" / "Private" / "JokateSourceControlProvider.cpp").is_file()
    assert "Source" in res["copied"]
    assert res["dest"].endswith("Plugins/JokateSourceControl")


def test_install_keeps_binaries_and_is_idempotent(tmp_path: Path):
    proj = tmp_path / "Proj"
    dest = proj / "Plugins" / "JokateSourceControl"
    (dest / "Binaries" / "Win64").mkdir(parents=True)
    marker = dest / "Binaries" / "Win64" / "old.dll"
    marker.write_text("x", encoding="utf-8")
    (dest / "Intermediate").mkdir()

    pluginmod.install(proj)
    assert marker.is_file()

    # 원본에 없는 옛 소스 파일은 재설치에서 사라진다
    stale = dest / "Source" / "JokateSourceControl" / "Private" / "Stale.cpp"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("// old", encoding="utf-8")

    res = pluginmod.install(proj)
    assert not stale.exists()
    assert marker.is_file()
    assert "Binaries" in res["kept"] and "Intermediate" in res["kept"]


def test_install_missing_source(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        pluginmod.install(tmp_path / "Proj", src=tmp_path / "nope")
