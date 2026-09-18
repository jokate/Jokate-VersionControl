"""
프로젝트 설정: <project>/.jokate/config.toml

[project]
content = "Content"            # 추적 루트 (프로젝트 기준 상대경로)

[tiers]
vendor = ["Paragon*", "SwordAnimsetPro"]   # 동결 등급 폴더 (Content 기준, glob)
ignore = ["Developers", "Collections"]      # 아예 추적하지 않음
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
"""


@dataclass
class Config:
    root: Path
    content: Path
    vendor: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=lambda: ["Developers", "Collections"])

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
    )


def init(project: str | Path) -> Path:
    root = Path(project).resolve()
    d = root / STATE_DIR
    d.mkdir(exist_ok=True)
    cfg = d / "config.toml"
    if not cfg.exists():
        cfg.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return cfg
