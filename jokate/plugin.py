"""UE 에디터 플러그인(JokateSourceControl) 설치.

ue-plugin/JokateSourceControl 을 <project>/Plugins/JokateSourceControl 로 복사한다.
Source·uplugin·Resources 만 갱신하고 Binaries·Intermediate 는 건드리지 않는다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

PLUGIN_NAME = "JokateSourceControl"
UPLUGIN = f"{PLUGIN_NAME}.uplugin"
COPY_ITEMS = ("Source", "Resources", UPLUGIN)
KEEP_ITEMS = ("Binaries", "Intermediate")


def source_dir() -> Path:
    """저장소 안의 플러그인 원본 폴더."""
    return Path(__file__).resolve().parents[1] / "ue-plugin" / PLUGIN_NAME


def install(project: str | Path, src: str | Path | None = None) -> dict:
    """플러그인을 프로젝트에 복사하고 결과를 dict 로 돌려준다."""
    src_path = Path(src) if src is not None else source_dir()
    if not (src_path / UPLUGIN).is_file():
        raise FileNotFoundError(f"플러그인 원본을 찾지 못했습니다: {src_path / UPLUGIN}")

    dest = Path(project) / "Plugins" / PLUGIN_NAME
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for item in COPY_ITEMS:
        s = src_path / item
        if not s.exists():
            continue
        d = dest / item
        if s.is_dir():
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        copied.append(item)

    kept = [n for n in KEEP_ITEMS if (dest / n).exists()]
    return {"dest": str(dest).replace("\\", "/"), "copied": copied, "kept": kept}


HINT = (
    "프로젝트를 다시 빌드하세요"
    "(에디터를 닫고 .uproject 우클릭 > Generate project files 후 빌드,"
    " 또는 에디터 실행 시 다시 빌드 묻는 창에서 예).\n"
    "에디터에서 리비전 컨트롤 > 프로바이더로 Jokate 선택"
)
