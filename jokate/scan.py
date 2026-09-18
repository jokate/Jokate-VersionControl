"""
Content 트리를 훑어 애셋 단위 레코드를 만든다.

- 등급(vendor/authored) 분류
- 헤더 파싱: 클래스, 이름, BP 부모, 하드 의존성, 저장 엔진 버전, 리다이렉터 여부
- 내용 해시(blake2b): authored 등급은 항상, vendor는 옵션
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import ASSET_EXTS, Config
from .uasset import read_package


@dataclass
class AssetRecord:
    rel: str                 # Content 기준 상대경로 (슬래시)
    package: str             # /Game/... 패키지 경로
    tier: str                # vendor | authored
    size: int
    mtime: float
    sha: str = ""            # blake2b-256 hex, 비어 있으면 미계산
    cls: str = ""            # 애셋 클래스 (Blueprint, Texture2D, ...)
    name: str = ""
    parent: str = ""         # Blueprint 부모 클래스
    deps: list[str] = field(default_factory=list)   # 하드 의존 패키지 (/Game/...)
    saved_by: str = ""       # 저장한 엔진 버전
    redirector: bool = False
    has_thumb: bool = False
    error: str = ""          # 파싱 실패 사유


def _hash_file(p: Path) -> str:
    h = hashlib.blake2b(digest_size=32)
    with open(p, "rb", buffering=1 << 20) as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _package_path(rel: Path) -> str:
    return "/Game/" + rel.with_suffix("").as_posix()


def _scan_one(cfg: Config, p: Path, tier: str, do_hash: bool) -> AssetRecord:
    rel = p.relative_to(cfg.content)
    st = p.stat()
    rec = AssetRecord(rel=rel.as_posix(), package=_package_path(rel), tier=tier,
                      size=st.st_size, mtime=st.st_mtime)
    try:
        pkg = read_package(p, thumbnails=True)
        rec.cls = pkg.asset_class()
        rec.name = pkg.asset_name()
        rec.parent = pkg.blueprint_parent()
        rec.deps = pkg.hard_dependencies()
        rec.saved_by = str(pkg.summary.saved_by) if pkg.summary.saved_by else ""
        rec.redirector = pkg.is_redirector()
        rec.has_thumb = bool(pkg.thumbnails)
    except Exception as e:  # 손상/미지원 포맷도 목록에는 남긴다
        rec.error = f"{type(e).__name__}: {e}"
    if do_hash:
        rec.sha = _hash_file(p)
    return rec


def scan(cfg: Config, *, hash_vendor: bool = False, workers: int = 8) -> list[AssetRecord]:
    jobs: list[tuple[Path, str]] = []
    for p in cfg.content.rglob("*"):
        if p.suffix.lower() not in ASSET_EXTS:
            continue
        tier = cfg.tier_of(p.relative_to(cfg.content))
        if tier is None:
            continue
        jobs.append((p, tier))

    def run(job: tuple[Path, str]) -> AssetRecord:
        p, tier = job
        return _scan_one(cfg, p, tier, do_hash=(tier == "authored" or hash_vendor))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        records = list(ex.map(run, jobs))
    records.sort(key=lambda r: r.rel)
    return records


def save(records: list[AssetRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"scanned_at": time.time(), "count": len(records),
               "assets": [asdict(r) for r in records]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def summarize(records: list[AssetRecord]) -> str:
    from collections import Counter, defaultdict
    lines = []
    by_tier: dict[str, list[AssetRecord]] = defaultdict(list)
    for r in records:
        by_tier[r.tier].append(r)
    for tier in ("authored", "vendor"):
        rs = by_tier.get(tier, [])
        if not rs:
            continue
        size = sum(r.size for r in rs)
        errs = sum(1 for r in rs if r.error)
        lines.append(f"[{tier}] {len(rs)}개  {size / 1_048_576:,.1f} MB  파싱실패 {errs}")
        cls = Counter(r.cls or "?" for r in rs)
        lines.append("   " + ", ".join(f"{c} {n}" for c, n in cls.most_common(12)))
        if tier == "authored":
            red = [r.rel for r in rs if r.redirector]
            if red:
                lines.append(f"   리다이렉터 {len(red)}개: " + ", ".join(red[:8]))
            # authored → vendor 의존 요약
            vendor_pkgs = {r.package for r in by_tier.get("vendor", [])}
            cross = Counter()
            for r in rs:
                for d in r.deps:
                    if d in vendor_pkgs:
                        cross[d.split("/")[2]] += 1
            if cross:
                lines.append("   vendor 의존: " + ", ".join(f"{k} {v}" for k, v in cross.most_common()))
    return "\n".join(lines)
