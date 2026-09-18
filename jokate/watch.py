"""
저장 감지 자동 스냅샷 데몬 (폴링, 외부 의존성 없음).

- authored 등급 파일의 (size, mtime) 맵을 interval 초마다 비교
- 변화 감지 후 debounce 초 동안 추가 변화가 없으면 Store.snap('') → auto 스냅샷
- 실제 변경 판단은 snap 내부 sha 비교에 맡긴다 (리세이브로 mtime만 바뀌면 '내용 동일, 건너뜀')
- 폴링 시 Content 전체 rglob 금지: 최상위 폴더를 tier_of 로 판정해 authored 폴더만 rglob
- 로직은 poll_once() 순수 함수로 분리 (시간 주입 가능)
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from .config import ASSET_EXTS, Config
from .store import Diff, Snapshot, Store, format_ts

Fingerprint = dict[str, tuple[int, float]]


@dataclass(frozen=True)
class WatchState:
    fp: Fingerprint = field(default_factory=dict)
    dirty_since: float | None = None     # 마지막 변화 감지 시각. None 이면 대기 중


@dataclass
class PollResult:
    snapshot: Snapshot | None            # None 이면 내용 동일(sha)로 snap 이 건너뜀
    diff: Diff


def fingerprint(cfg: Config) -> Fingerprint:
    """authored 폴더만 rglob 해서 rel → (size, mtime). Content 최상위 파일도 authored."""
    out: Fingerprint = {}
    content = cfg.content
    if not content.is_dir():
        return out
    for top in content.iterdir():
        if top.is_dir():
            if cfg.tier_of(Path(top.name, "_")) != "authored":
                continue
            it = top.rglob("*")
        else:
            it = (top,)
        for p in it:
            if p.suffix.lower() not in ASSET_EXTS or not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.relative_to(content).as_posix()] = (st.st_size, st.st_mtime)
    return out


def poll_once(store: Store, state: WatchState, now: float, debounce: float,
              *, fp: Fingerprint | None = None) -> tuple[WatchState, PollResult | None]:
    """한 번 폴링. 반환 (새 상태, PollResult|None). PollResult 는 snap 을 시도했을 때만."""
    cur = fingerprint(store.cfg) if fp is None else fp
    if cur != state.fp:
        return WatchState(cur, now), None
    if state.dirty_since is None or now - state.dirty_since < debounce:
        return state, None
    snap, d, _ = store.snap("")
    return replace(state, dirty_since=None), PollResult(snap, d)


def format_line(r: PollResult, now: float | None = None) -> str:
    ts = format_ts(now if now is not None else time.time())
    if r.snapshot is None:
        return f"{ts}  (내용 동일, 건너뜀)"
    bc = r.diff.by_class()
    parts = []
    for cls in sorted(bc):
        c = bc[cls]
        parts.append(f"{cls} " + "/".join(f"{k[0].upper()}{c[k]}" for k in ("added", "modified", "moved", "deleted") if c[k]))
    line = f"{ts}  #{r.snapshot.id} {r.snapshot.kind}  " + ("; ".join(p for p in parts if not p.endswith(" ")) or "(초기)")
    n_resave = len(r.diff.resave)
    if n_resave:
        line += f"  리세이브만 {n_resave}"
    return line


def run(cfg: Config, interval: float = 2.0, debounce: float = 5.0, *, out=None) -> int:
    out = out or sys.stdout
    log_path = cfg.state_dir / "watch.log"
    st = Store(cfg)

    def emit(line: str) -> None:
        print(line, file=out, flush=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    try:
        # 시작 시 즉시 snap 한 번
        snap, d, _ = st.snap("")
        emit(format_line(PollResult(snap, d)))
        state = WatchState(fingerprint(cfg), None)
        emit(f"{format_ts(time.time())}  watch 시작: {cfg.content}  interval={interval}s debounce={debounce}s  (Ctrl+C 종료)")
        while True:
            time.sleep(interval)
            state, r = poll_once(st, state, time.time(), debounce)
            if r is not None:
                emit(format_line(r))
    except KeyboardInterrupt:
        emit(f"{format_ts(time.time())}  watch 종료")
        return 0
    finally:
        st.close()
