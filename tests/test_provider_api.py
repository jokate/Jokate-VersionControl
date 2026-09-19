"""22a: UE 리비전 컨트롤 프로바이더용 API (states·history·extract·editor_managed·ping)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402
from jokate import web  # noqa: E402


@pytest.fixture
def st(tmp_path: Path) -> storemod.Store:
    root = tmp_path / "Proj"
    foo = root / "Content" / "Foo"
    foo.mkdir(parents=True)
    (root / "Content" / "Paragon" / "Sub").mkdir(parents=True)
    cfgmod.init(root)
    (root / ".jokate" / "config.toml").write_text(
        '[project]\ncontent = "Content"\n[tiers]\nvendor = ["Paragon*"]\n'
        'ignore = ["Developers"]\n', encoding="utf-8")
    (foo / "A.uasset").write_bytes(b"AAAA-v1")
    (foo / "B.uasset").write_bytes(b"BBBB-v1")
    (root / "Content" / "Paragon" / "Sub" / "V.uasset").write_bytes(b"VVVV")
    s = storemod.Store(cfgmod.load(root))
    web.api_confirm(s, "처음 확정")          # #1 fix
    return s


def test_states_reports_each_state(st: storemod.Store) -> None:
    c = st.cfg.content
    (c / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")     # modified
    (c / "Foo" / "C.uasset").write_bytes(b"CCCC")        # added
    (c / "Foo" / "B.uasset").unlink()                    # deleted
    (c / "Foo" / "Keep.uasset").write_bytes(b"KEEP")
    web.api_confirm(st, "Keep 확정", ["Foo/Keep.uasset"])
    r = web.api_states(st, ["Foo/A.uasset", "Foo/C.uasset", "Foo/B.uasset", "Foo/Keep.uasset",
                            "Paragon/Sub/V.uasset", "../Outside.uasset", "Foo/Nope.uasset",
                            "\\Foo\\A.uasset", "Foo/readme.txt"])
    s = r["states"]
    assert s["Foo/A.uasset"]["state"] == "modified"
    assert s["Foo/A.uasset"]["baseline_sha"] and s["Foo/A.uasset"]["sha"] != s["Foo/A.uasset"]["baseline_sha"]
    assert s["Foo/C.uasset"]["state"] == "added" and s["Foo/C.uasset"]["baseline_sha"] == ""
    assert s["Foo/B.uasset"]["state"] == "deleted" and s["Foo/B.uasset"]["sha"] == ""
    assert s["Foo/Keep.uasset"]["state"] == "clean"
    assert s["Paragon/Sub/V.uasset"]["state"] == "untracked"
    assert s["../Outside.uasset"]["state"] == "untracked"
    assert s["Foo/readme.txt"]["state"] == "untracked"
    assert s["Foo/Nope.uasset"]["state"] == "missing"
    assert s["Foo/A.uasset"]["tier"] == "authored" and s["Foo/A.uasset"]["size"] == 7
    assert r["head_fix"]["message"] == "Keep 확정"


def test_states_all_when_rels_empty(st: storemod.Store) -> None:
    (st.cfg.content / "Foo" / "C.uasset").write_bytes(b"CCCC")
    (st.cfg.content / "Foo" / "B.uasset").unlink()
    s = web.api_states(st, [])["states"]
    assert set(s) == {"Foo/A.uasset", "Foo/B.uasset", "Foo/C.uasset"}   # vendor 는 빠진다
    assert s["Foo/A.uasset"]["state"] == "clean"
    assert s["Foo/B.uasset"]["state"] == "deleted" and s["Foo/C.uasset"]["state"] == "added"


def test_states_ignore_auto_snapshot(st: storemod.Store) -> None:
    (st.cfg.content / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")
    st.snap("")                                    # 자동 스냅샷(journal) — baseline 은 그대로
    s = web.api_states(st, ["Foo/A.uasset"])["states"]
    assert s["Foo/A.uasset"]["state"] == "modified"


def test_history_only_fixes(st: storemod.Store) -> None:
    a = st.cfg.content / "Foo" / "A.uasset"
    a.write_bytes(b"AAAA-v2")
    st.snap("")                                    # 작업 중 기록 — 이력에 안 나온다
    web.api_confirm(st, "두 번째", ["Foo/A.uasset"])
    a.unlink()
    web.api_confirm(st, "삭제 확정", ["Foo/A.uasset"])
    h = web.api_history(st, "\\Foo/A.uasset")
    assert [x["action"] for x in h] == ["delete", "edit", "add"]
    assert [x["revision"] for x in h] == [3, 2, 1]
    assert [x["message"] for x in h] == ["삭제 확정", "두 번째", "처음 확정"]
    assert h[-1]["sha"] and h[0]["sha"] == "" and h[0]["size"] == 0
    assert web.api_history(st, "Foo/A.uasset", 1) == h[:1]
    assert web.api_history(st, "Foo/Nope.uasset") == []


def test_extract_writes_saved_dir_and_reuses(st: storemod.Store) -> None:
    sha = web.api_states(st, ["Foo/A.uasset"])["states"]["Foo/A.uasset"]["sha"]
    p = web.api_extract(st, "Foo/A.uasset", sha)["path"]
    assert "\\" not in p
    assert p.endswith(f"Saved/JokateDiff/{sha[:8]}/A.uasset")
    assert Path(p).read_bytes() == b"AAAA-v1"
    assert web.api_extract(st, "Foo/A.uasset", sha)["path"] == p      # 재사용
    with pytest.raises(ValueError):
        web.api_extract(st, "Foo/A.uasset", "zzzz")
    with pytest.raises(KeyError):
        web.api_extract(st, "Foo/A.uasset", "ab" * 32)


def test_editor_managed_skips_bridge(st: storemod.Store, monkeypatch: pytest.MonkeyPatch) -> None:
    from jokate import bridge as bridgemod
    monkeypatch.setattr(storemod, "editor_running", lambda *a, **k: True)

    def boom(*a, **k):
        raise AssertionError("브릿지를 호출하면 안 된다")

    monkeypatch.setattr(bridgemod, "request", boom)
    monkeypatch.setattr(bridgemod, "bridge_alive", boom)
    (st.cfg.content / "Foo" / "A.uasset").write_bytes(b"AAAA-v2")
    web.api_confirm(st, "에디터 확정", ["Foo/A.uasset"], True)
    (st.cfg.content / "Foo" / "A.uasset").write_bytes(b"AAAA-v3")
    r = web.api_discard_apply(st, [], False, True)                   # assets 비면 전부
    assert r["ok"] is True and r["reloaded"] is None
    assert (st.cfg.content / "Foo" / "A.uasset").read_bytes() == b"AAAA-v2"
    assert web.api_states(st, ["Foo/A.uasset"])["states"]["Foo/A.uasset"]["state"] == "clean"


def test_discard_without_editor_managed_still_checks_bridge(
        st: storemod.Store, monkeypatch: pytest.MonkeyPatch) -> None:
    from jokate import bridge as bridgemod
    monkeypatch.setattr(storemod, "editor_running", lambda *a, **k: True)
    monkeypatch.setattr(bridgemod, "bridge_alive", lambda *a, **k: False)
    (st.cfg.content / "Foo" / "A.uasset").write_bytes(b"AAAA-v9")
    with pytest.raises(storemod.RestoreBlocked):
        web.api_discard_apply(st, [], False, False)


def test_ping(st: storemod.Store) -> None:
    p = web.api_ping(st.cfg)
    assert p["ok"] is True and p["api"] == 1 and p["project"] == "Proj"
    assert p["root"] == st.cfg.root.as_posix() and p["content"] == st.cfg.content.as_posix()
    assert "\\" not in p["root"] and p["port"] == st.cfg.port and p["build"]
