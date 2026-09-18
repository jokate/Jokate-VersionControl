"""
프로젝트 설정: <project>/.jokate/config.toml

[project]
content = "Content"            # 추적 루트 (프로젝트 기준 상대경로)

[tiers]
vendor = ["Paragon*", "SwordAnimsetPro"]   # 동결 등급 폴더 (Content 기준, glob)
ignore = ["Developers", "Collections"]      # 아예 추적하지 않음

[web]
port = 8765                    # serve 포트 (--port 미지정 시). 에디터 메뉴도 이 값을 읽는다
"""
from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

STATE_DIR = ".jokate"
ASSET_EXTS = {".uasset", ".umap"}

DEFAULT_CONFIG = """[project]
content = "Content"

[tiers]
# Content/ 바로 아래 폴더 이름 기준 glob. 여기 걸리면 vendor(동결) 등급.
vendor = []
# 추적 제외
ignore = ["Developers", "Collections"]

[web]
# serve 포트 (--port 미지정 시). 에디터 우클릭 메뉴도 이 값으로 접속
port = 8765

[daemon]
# 데몬이 윈도우 트레이 아이콘을 띄울지
tray = true

[editor]
# 에디터를 켤 때 데몬을 자동으로 띄울지 (bridge-install 시점에 tool.json 에 기록된다)
autostart = true
# 'UE 에서 diff 열기' 에 쓸 UnrealEditor.exe 경로. 비워 두면 .uproject 의 엔진 버전으로 자동 탐색
exe = ""

[retention]
# 이보다 오래된 자동 스냅샷은 정리 대상 (0 이면 정리 안 함). 라벨 스냅샷은 절대 안 지운다
auto_days = 14
# 오래됐어도 최신 자동 스냅샷 이만큼은 남긴다 (0 이면 나이 기준만 적용)
keep_last_auto = 30
"""
DEFAULT_PORT = 8765


@dataclass
class Config:
    root: Path
    content: Path
    vendor: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=lambda: ["Developers", "Collections"])
    port: int = DEFAULT_PORT
    tray: bool = True          # [daemon] tray — 데몬이 트레이 아이콘을 띄울지
    autostart: bool = True     # [editor] autostart — 에디터가 데몬을 자동 실행할지
    editor_exe: str = ""       # [editor] exe — UnrealEditor.exe 경로 (빈 문자열이면 자동 탐색)
    auto_days: int = 14        # [retention] auto_days — 자동 스냅샷 보관기간(일, 0=정리 안 함)
    keep_last_auto: int = 30   # [retention] keep_last_auto — 최신 자동 스냅샷 보존 개수

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    def tier_of(self, rel: Path) -> str | None:
        """Content 기준 상대경로 → 'vendor' | 'authored' | None(무시)."""
        top = rel.parts[0] if len(rel.parts) > 1 else ""
        for pat in self.ignore:
            if fnmatch.fnmatch(top, pat):
                return None
        for pat in self.vendor:
            if fnmatch.fnmatch(top, pat):
                return "vendor"
        return "authored"


def load(project: str | Path) -> Config:
    root = Path(project).resolve()
    cfg_path = root / STATE_DIR / "config.toml"
    data: dict = {}
    if cfg_path.exists():
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    content = root / data.get("project", {}).get("content", "Content")
    tiers = data.get("tiers", {})
    return Config(
        root=root,
        content=content,
        vendor=list(tiers.get("vendor", [])),
        ignore=list(tiers.get("ignore", ["Developers", "Collections"])),
        port=int(data.get("web", {}).get("port", DEFAULT_PORT)),
        tray=bool(data.get("daemon", {}).get("tray", True)),
        autostart=bool(data.get("editor", {}).get("autostart", True)),
        editor_exe=str(data.get("editor", {}).get("exe", "") or "").strip(),
        auto_days=int(data.get("retention", {}).get("auto_days", 14)),
        keep_last_auto=int(data.get("retention", {}).get("keep_last_auto", 30)),
    )


def init(project: str | Path) -> Path:
    root = Path(project).resolve()
    d = root / STATE_DIR
    d.mkdir(exist_ok=True)
    cfg = d / "config.toml"
    if not cfg.exists():
        cfg.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return cfg
