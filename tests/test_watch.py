"""watch.poll_once: 파일 변경 → debounce 경과 후 auto 스냅샷, 내용 동일 재저장은 스냅샷 없음."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402
from jokate import watch  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "Proj"
    (root / "Content" / "Foo").mkdir(parents=True)
    (root / "Content" / "Developers").mkdir()
    cfgmod.init(root)
    (root / "Content" / "Foo" / "A.uasset").write_bytes(b"AAAA-v1")
    (root / "Content" / "Developers" / "X.uasset").write_bytes(b"ignored")
    return root


def test_fingerprint_authored_only(project: Path) -> None:
    fp = watch.fingerprint(cfgmod.load(project))
    assert set(fp) == {"Foo/A.uasset"}


def test_poll_snapshots_after_debounce(project: Path) -> None:
    cfg = cfgmod.load(project)
    st = storemod.Store(cfg)
    st.snap("")  # 시작 스냅샷
    state = watch.WatchState(watch.fingerprint(cfg), None)
    a = project / "Content" / "Foo" / "A.uasset"

    # 변화 없음
    state, r = watch.poll_once(st, state, 100.0, 5.0)
    assert r is None and state.dirty_since is None

    # 파일 변경 감지 → dirty
    a.write_bytes(b"AAAA-v2")
    os.utime(a, (200, 200))
    state, r = watch.poll_once(st, state, 101.0, 5.0)
    assert r is None and state.dirty_since == 101.0

    # debounce 전에는 스냅샷 없음
    state, r = watch.poll_once(st, state, 103.0, 5.0)
    assert r is None and state.dirty_since == 101.0

    # 추가 변화 → dirty 갱신
    (project / "Content" / "Foo" / "B.uasset").write_bytes(b"BBBB")
    state, r = watch.poll_once(st, state, 104.0, 5.0)
    assert r is None and state.dirty_since == 104.0

    # debounce 경과 → auto 스냅샷
    state, r = watch.poll_once(st, state, 109.5, 5.0)
    assert r is not None and r.snapshot is not None
    assert r.snapshot.kind == "auto" and r.snapshot.id == 2
    assert len(r.diff.modified) == 1 and len(r.diff.added) == 1
    assert state.dirty_since is None
    line = watch.format_line(r, 109.5)
    assert "#2 auto" in line and "M1" in line and "A1" in line

    # 내용 동일 재저장(mtime 만 변경) → snap 이 건너뜀
    os.utime(a, (300, 300))
    state, r = watch.poll_once(st, state, 110.0, 5.0)
    assert r is None and state.dirty_since == 110.0
    state, r = watch.poll_once(st, state, 116.0, 5.0)
    assert r is not None and r.snapshot is None
    assert "내용 동일" in watch.format_line(r, 116.0)
    assert st.head().id == 2
    st.close()
