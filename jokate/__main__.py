"""
jokate CLI

  python -m jokate init <project>
  python -m jokate scan <project> [--hash-vendor] [--json]
  python -m jokate inspect <file.uasset> [--thumb out.png]
  python -m jokate table <project> [--tier authored] [--cls Blueprint]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import config as cfgmod
from . import scan as scanmod
from .uasset import read_package


def cmd_init(a: argparse.Namespace) -> int:
    p = cfgmod.init(a.project)
    print(f"created {p}")
    return 0


def cmd_scan(a: argparse.Namespace) -> int:
    cfg = cfgmod.load(a.project)
    t0 = time.time()
    recs = scanmod.scan(cfg, hash_vendor=a.hash_vendor)
    out = cfg.state_dir / "scan.json"
    scanmod.save(recs, out)
    print(scanmod.summarize(recs))
    print(f"→ {out}  ({time.time() - t0:.1f}s)")
    return 0


def cmd_inspect(a: argparse.Namespace) -> int:
    pkg = read_package(a.file, thumbnails=True)
    s = pkg.summary
    print(f"{pkg.path.name}")
    print(f"  saved_by={s.saved_by}  legacy={s.legacy_version} ue4={s.ver_ue4} ue5={s.ver_ue5}")
    print(f"  asset={pkg.asset_name()}  class={pkg.asset_class()}  parent={pkg.blueprint_parent() or '-'}")
    print(f"  names={s.name_count} imports={s.import_count} exports={s.export_count} header={s.total_header_size}")
    print("  deps:")
    for d in pkg.hard_dependencies():
        print(f"    {d}")
    for t in pkg.thumbnails:
        print(f"  thumb: {t.class_name} {t.width}x{t.height} {t.fmt} {len(t.data)}B")
    if a.thumb and pkg.thumbnails:
        Path(a.thumb).write_bytes(pkg.thumbnails[0].data)
        print(f"  → {a.thumb}")
    return 0


def cmd_table(a: argparse.Namespace) -> int:
    cfg = cfgmod.load(a.project)
    data = json.loads((cfg.state_dir / "scan.json").read_text(encoding="utf-8"))
    rows = data["assets"]
    if a.tier:
        rows = [r for r in rows if r["tier"] == a.tier]
    if a.cls:
        rows = [r for r in rows if r["cls"] == a.cls]
    rows.sort(key=lambda r: (r["cls"], r["rel"]))
    print(f"{'class':<26} {'asset':<34} {'size':>8}  {'saved':<7} deps  parent/error")
    for r in rows:
        extra = r["parent"] or r["error"][:40]
        print(f"{r['cls'] or '?':<26} {r['name'][:34]:<34} {r['size'] / 1024:>7.0f}K  {r['saved_by']:<7} {len(r['deps']):>4}  {extra}")
    print(f"{len(rows)} rows")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jokate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init"); s.add_argument("project"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("scan"); s.add_argument("project"); s.add_argument("--hash-vendor", action="store_true")
    s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("inspect"); s.add_argument("file"); s.add_argument("--thumb"); s.set_defaults(fn=cmd_inspect)
    s = sub.add_parser("table"); s.add_argument("project"); s.add_argument("--tier"); s.add_argument("--cls")
    s.set_defaults(fn=cmd_table)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
