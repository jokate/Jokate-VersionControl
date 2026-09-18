"""
jokate CLI

  python -m jokate init <project>
  python -m jokate scan <project> [--hash-vendor] [--json]
  python -m jokate inspect <file.uasset> [--thumb out.png]
  python -m jokate table <project> [--tier authored] [--cls Blueprint]
  python -m jokate snap <project> [-m 메시지]      # authored 스냅샷 (-m 없으면 auto)
  python -m jokate log <project>
  python -m jokate show <project> <id>             # 직전 스냅샷 대비 변경
  python -m jokate restore <project> <id> [--asset rel ...] [--apply] [--discard-dirty]   # 롤백 (기본 드라이런)
  python -m jokate squash <project> <from_id> <to_id> [-m 메시지] [--include-labels]   # 연속 스냅샷 묶기
  python -m jokate prune <project> [--days N] [--keep N] [--dry-run]  # 오래된 auto 스냅샷 정리
  python -m jokate gc <project> [--dry-run]         # 참조 없는 객체 삭제
  python -m jokate bridge-install <project>      # 에디터 브릿지 스크립트를 Content/Python 에 설치
  python -m jokate bridge-status <project>         # 브릿지 heartbeat 나이
  python -m jokate watch <project> [--interval 2] [--debounce 5]        # 저장 감지 자동 스냅샷
  python -m jokate serve <project> [--port 8765]                        # 타임라인 웹 UI
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


def cmd_snap(a: argparse.Namespace) -> int:
    from . import store as storemod
    cfg = cfgmod.load(a.project)
    st = storemod.Store(cfg)
    t0 = time.time()
    try:
        snap, d, stored = st.snap(a.message or "", force=a.force, only=a.only or None)
    except KeyError as e:
        print(e, file=sys.stderr)
        return 1
    if snap is None:
        print("변경 없음 — 스냅샷을 만들지 않았다")
        return 0
    from . import meta as metamod
    saved, _ = metamod.capture_for_snapshot(st, d)
    print(f"snapshot #{snap.id} ({snap.kind}) {snap.message}".rstrip())
    print(storemod.format_diff(d))
    extra = f", 내용 기록 {saved}개" if saved else ""
    print(f"새 객체 {stored}개{extra}  ({time.time() - t0:.1f}s)")
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    from . import store as storemod
    cfg = cfgmod.load(a.project)
    st = storemod.Store(cfg)
    d = st.status()
    if d.empty:
        print("올릴 변경 없음")
        return 0
    head = st.head()
    print(f"HEAD #{head.id if head else '-'} 대비 올리지 않은 변경:")
    print(storemod.format_diff(d))
    return 0


def cmd_log(a: argparse.Namespace) -> int:
    from . import store as storemod
    cfg = cfgmod.load(a.project)
    st = storemod.Store(cfg)
    snaps = st.log()
    if not snaps:
        print("스냅샷 없음")
        return 0
    for s in snaps:
        n = st.db.execute("SELECT COUNT(*) FROM tree WHERE snapshot_id=?", (s.id,)).fetchone()[0]
        _, d = st.show(s.id)
        tag = "  (리세이브만)" if d.all_noise else ""
        print(f"#{s.id:<4} {storemod.format_ts(s.ts)}  {s.kind:<5} {n:>5}개  {s.message}{tag}")
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    from . import store as storemod
    cfg = cfgmod.load(a.project)
    st = storemod.Store(cfg)
    try:
        s, d = st.show(a.id)
    except KeyError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"snapshot #{s.id} ({s.kind}) {storemod.format_ts(s.ts)}  parent={s.parent or '-'}  {s.message}".rstrip())
    print(storemod.format_diff(d))
    return 0


def cmd_restore(a: argparse.Namespace) -> int:
    from . import store as storemod
    cfg = cfgmod.load(a.project)
    st = storemod.Store(cfg)
    try:
        plan = st.plan_restore(a.id, a.asset or None)
    except KeyError as e:
        print(e, file=sys.stderr)
        return 1
    print(storemod.format_restore(plan))
    if not a.apply:
        print("(드라이런 — 적용하려면 --apply)")
        return 0
    t0 = time.time()
    try:
        r = st.apply_restore(plan, discard_dirty=a.discard_dirty)
    except (RuntimeError, FileNotFoundError) as e:
        print(e, file=sys.stderr)
        return 2
    reload = f", 에디터 reload {r.reloaded}개" if r.reloaded is not None else ""
    print(f"안전 스냅샷 #{r.safety.id} ({r.safety.message}) → 파일 {r.written}개 씀, {r.deleted}개 삭제{reload}"
          f" → 스냅샷 #{r.result.id} ({r.result.message})  ({time.time() - t0:.1f}s)")
    return 0


def cmd_squash(a: argparse.Namespace) -> int:
    from . import store as storemod
    cfg = cfgmod.load(a.project)
    st = storemod.Store(cfg)
    lo, hi = min(a.from_id, a.to_id), max(a.from_id, a.to_id)
    chain: list[int] = []
    sid: int | None = hi
    while sid is not None and sid >= lo:
        s = st.get(sid)
        if s is None:
            print(f"snapshot {sid} 없음", file=sys.stderr)
            return 1
        chain.append(sid)
        if sid == lo:
            break
        sid = s.parent
    chain.reverse()
    if not chain or chain[0] != lo:
        print(f"#{lo} 에서 #{hi} 로 이어지는 사슬이 없다", file=sys.stderr)
        return 1
    try:
        snap, removed = st.squash(chain, a.message or "", include_labels=bool(a.include_labels))
    except storemod.SquashHasLabels as e:
        print("이름 붙인 스냅샷이 함께 사라진다:", file=sys.stderr)
        for i, m in e.labels:
            print(f"  #{i} {m}".rstrip(), file=sys.stderr)
        print("그래도 묶으려면 --include-labels 로 다시 실행", file=sys.stderr)
        return 2
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"snapshot #{snap.id} ({snap.kind}) {snap.message}".rstrip())
    print(f"묶음: {len(chain)}개 → 1개, 스냅샷 {removed}개 삭제")
    return 0


def cmd_prune(a: argparse.Namespace) -> int:
    from . import store as storemod
    cfg = cfgmod.load(a.project)
    st = storemod.Store(cfg)
    days = a.days if a.days is not None else cfg.auto_days
    keep = a.keep if a.keep is not None else cfg.keep_last_auto
    ids = st.prune(auto_days=days, keep_last_auto=keep, dry_run=a.dry_run)
    if not ids:
        print("정리할 자동 스냅샷 없음")
        return 0
    head = "지울 대상" if a.dry_run else "삭제"
    print(f"{head}: 자동 스냅샷 {len(ids)}개  " + ", ".join(f"#{i}" for i in ids[:20])
          + (" …" if len(ids) > 20 else ""))
    if a.dry_run:
        n, size = st.gc(dry_run=True, exclude_snapshots=ids)
        print(f"(드라이런 — 객체 {n}개 {size / 1048576:.1f} MB 가 함께 지워진다)")
    return 0


def cmd_gc(a: argparse.Namespace) -> int:
    from . import store as storemod
    st = storemod.Store(cfgmod.load(a.project))
    n, size = st.gc(dry_run=a.dry_run)
    head = "지울 객체" if a.dry_run else "지운 객체"
    print(f"{head} {n}개  {size / 1048576:.1f} MB")
    return 0


def cmd_bridge_install(a: argparse.Namespace) -> int:
    from . import bridge
    done = bridge.install(Path(a.project))
    print("\n".join(done) if done else "이미 설치됨")
    print("에디터를 (재)시작하거나 Python 콘솔에서 `import jokate_bridge` 실행")
    return 0


def cmd_bridge_status(a: argparse.Namespace) -> int:
    from . import bridge
    from . import store as storemod
    cfg = cfgmod.load(a.project)
    age = bridge.heartbeat_age(cfg)
    editor = "실행 중" if storemod.editor_running() else "꺼짐"
    if age is None:
        print(f"브릿지 없음 (heartbeat 없음)  에디터: {editor}")
        return 1
    alive = age <= bridge.HEARTBEAT_MAX_AGE
    print(f"heartbeat {age:.1f}s 전 → {'살아 있음' if alive else '끊김'}  에디터: {editor}")
    return 0 if alive else 1


def cmd_watch(a: argparse.Namespace) -> int:
    from . import watch as watchmod
    cfg = cfgmod.load(a.project)
    return watchmod.run(cfg, interval=a.interval, debounce=a.debounce)


def cmd_serve(a: argparse.Namespace) -> int:
    from . import web as webmod
    cfg = cfgmod.load(a.project)
    return webmod.serve(cfg, port=a.port if a.port is not None else cfg.port)


def cmd_daemon(a: argparse.Namespace) -> int:
    from . import daemon as daemonmod
    cfg = cfgmod.load(a.project)
    return daemonmod.run(cfg, port=a.port, interval=a.interval, debounce=a.debounce)


def cmd_daemon_stop(a: argparse.Namespace) -> int:
    from . import daemon as daemonmod
    return daemonmod.stop_remote(cfgmod.load(a.project))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jokate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init"); s.add_argument("project"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("scan"); s.add_argument("project"); s.add_argument("--hash-vendor", action="store_true")
    s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("inspect"); s.add_argument("file"); s.add_argument("--thumb"); s.set_defaults(fn=cmd_inspect)
    s = sub.add_parser("table"); s.add_argument("project"); s.add_argument("--tier"); s.add_argument("--cls")
    s.set_defaults(fn=cmd_table)
    s = sub.add_parser("snap"); s.add_argument("project"); s.add_argument("-m", "--message")
    s.add_argument("--force", action="store_true", help="변경 없어도 스냅샷 생성")
    s.add_argument("--only", nargs="+", metavar="REL", help="이 애셋만 올림(부분 스냅샷, 나머지는 HEAD 유지)")
    s.set_defaults(fn=cmd_snap)
    s = sub.add_parser("status"); s.add_argument("project"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("log"); s.add_argument("project"); s.set_defaults(fn=cmd_log)
    s = sub.add_parser("show"); s.add_argument("project"); s.add_argument("id", type=int); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("restore"); s.add_argument("project"); s.add_argument("id", type=int)
    s.add_argument("--asset", action="append", metavar="REL", help="이 애셋만 되돌림 (Content 기준 상대경로, 반복 가능)")
    s.add_argument("--apply", action="store_true", help="실제 적용 (기본은 드라이런)")
    s.add_argument("--discard-dirty", action="store_true", help="에디터에 저장 안 된 대상 패키지가 있어도 덮어씀")
    s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("squash", help="연속된 스냅샷을 하나로 묶고 이름 붙이기")
    s.add_argument("project"); s.add_argument("from_id", type=int); s.add_argument("to_id", type=int)
    s.add_argument("-m", "--message", help="묶은 스냅샷 이름")
    s.add_argument("--include-labels", action="store_true",
                   help="사슬 안의 이름 붙인 스냅샷도 함께 합쳐 지움")
    s.set_defaults(fn=cmd_squash)
    s = sub.add_parser("prune", help="오래된 자동 스냅샷 정리 (라벨은 안 지움)")
    s.add_argument("project")
    s.add_argument("--days", type=int, default=None, help="기본: config.toml [retention] auto_days")
    s.add_argument("--keep", type=int, default=None, help="기본: config.toml [retention] keep_last_auto")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_prune)
    s = sub.add_parser("gc", help="참조 없는 객체 파일 삭제"); s.add_argument("project")
    s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_gc)
    s = sub.add_parser("bridge-install"); s.add_argument("project"); s.set_defaults(fn=cmd_bridge_install)
    s = sub.add_parser("bridge-status"); s.add_argument("project"); s.set_defaults(fn=cmd_bridge_status)
    s = sub.add_parser("watch"); s.add_argument("project")
    s.add_argument("--interval", type=float, default=2.0, help="폴링 주기(초)")
    s.add_argument("--debounce", type=float, default=5.0, help="마지막 변화 후 이만큼 조용하면 스냅샷(초)")
    s.set_defaults(fn=cmd_watch)
    s = sub.add_parser("serve"); s.add_argument("project")
    s.add_argument("--port", type=int, default=None, help="기본: config.toml [web] port (8765)"); s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("daemon", help="웹 UI + 자동 스냅샷을 한 프로세스로 (창 없음)"); s.add_argument("project")
    s.add_argument("--port", type=int, default=None, help="기본: config.toml [web] port (8765)")
    s.add_argument("--interval", type=float, default=2.0)
    s.add_argument("--debounce", type=float, default=5.0)
    s.set_defaults(fn=cmd_daemon)
    s = sub.add_parser("daemon-stop", help="실행 중인 데몬 종료"); s.add_argument("project")
    s.set_defaults(fn=cmd_daemon_stop)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
