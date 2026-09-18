"""의미 diff: 사이드카 저장/읽기, diff_tables, capture_meta, api_metadiff, gc."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import config as cfgmod  # noqa: E402
from jokate import meta as metamod  # noqa: E402
from jokate import store as storemod  # noqa: E402
from jokate import web  # noqa: E402


def _tbl(rows, columns=("Atk", "Hp")):
    return {"kind": "DataTable", "row_struct": "FMonsterRow",
            "columns": list(columns), "rows": rows}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "Proj"
    (root / "Content" / "Data").mkdir(parents=True)
    cfgmod.init(root)
    (root / "Content" / "Data" / "DT_Monster.uasset").write_bytes(b"DT-v1")
    return root


@pytest.fixture
def st(project: Path):
    s = storemod.Store(cfgmod.load(project))
    yield s
    s.close()


# ---- diff_tables ----
def test_diff_cell_change() -> None:
    a = _tbl({"Goblin": {"Atk": "30", "Hp": "100"}})
    b = _tbl({"Goblin": {"Atk": "45", "Hp": "100"}})
    d = metamod.diff_tables(a, b)
    assert d["rows_changed"] == [{"row": "Goblin", "cells": [{"col": "Atk", "old": "30", "new": "45"}]}]
    assert d["unchanged"] == 0 and not d["rows_added"] and not d["rows_removed"]


def test_diff_row_add_remove_and_column() -> None:
    a = _tbl({"Goblin": {"Atk": "30", "Hp": "100"}, "Orc": {"Atk": "50", "Hp": "200"}})
    b = _tbl({"Goblin": {"Atk": "30", "Hp": "100", "Def": "5"}, "Slime": {"Atk": "1", "Hp": "9", "Def": "0"}},
             columns=("Atk", "Hp", "Def"))
    d = metamod.diff_tables(a, b)
    assert d["columns_added"] == ["Def"] and d["columns_removed"] == []
    assert [r["row"] for r in d["rows_added"]] == ["Slime"]
    assert [r["row"] for r in d["rows_removed"]] == ["Orc"]
    assert d["rows_changed"][0]["cells"] == [{"col": "Def", "old": "", "new": "5"}]


def test_diff_identical_and_column_removed() -> None:
    a = _tbl({"Goblin": {"Atk": "30", "Hp": "100"}})
    assert metamod.diff_tables(a, a) == {"columns_added": [], "columns_removed": [], "rows_added": [],
                                         "rows_removed": [], "rows_changed": [], "unchanged": 1}
    b = _tbl({"Goblin": {"Atk": "30"}}, columns=("Atk",))
    d = metamod.diff_tables(a, b)
    assert d["columns_removed"] == ["Hp"]
    assert d["rows_changed"][0]["cells"] == [{"col": "Hp", "old": "100", "new": ""}]


# ---- 저장/읽기 ----
def test_save_load_roundtrip(st) -> None:
    sha = "ab" * 32
    assert metamod.load_meta(st, sha) is None and not metamod.has_meta(st, sha)
    data = _tbl({"Goblin": {"Atk": "30", "Hp": "100"}})
    p = metamod.save_meta(st, sha, data)
    assert p == metamod.meta_path(st, sha) and p.parent.name == "ab"
    assert metamod.load_meta(st, sha) == data and metamod.has_meta(st, sha)


# ---- capture_meta ----
def _fake_request(payload, calls):
    def fn(cfg, op, packages, *a, **kw):
        calls.append((op, list(packages)))
        return {"ok": True, "meta": {p: payload for p in packages}, "skipped": [], "errors": {}}
    return fn


def test_capture_meta_saves_and_skips(st, project: Path) -> None:
    entry = [e for e in st._work_tree().values()][0]
    entry.cls = "DataTable"
    calls = []
    tbl = _tbl({"Goblin": {"Atk": "30", "Hp": "100"}})
    saved, skipped = metamod.capture_meta(st, [entry], _fake_request(tbl, calls))
    assert (saved, skipped) == (1, 0)
    assert calls == [("export_meta", ["/Game/Data/DT_Monster"])]
    assert metamod.load_meta(st, entry.sha) == tbl
    # 이미 있는 사이드카는 다시 요청하지 않는다
    calls.clear()
    assert metamod.capture_meta(st, [entry], _fake_request(tbl, calls)) == (0, 0)
    assert calls == []


def test_capture_meta_discards_when_file_changed(st, project: Path) -> None:
    entry = [e for e in st._work_tree().values()][0]
    entry.cls = "DataTable"
    tbl = _tbl({"Goblin": {"Atk": "30"}}, columns=("Atk",))

    def fn(cfg, op, packages, *a, **kw):
        (project / "Content" / "Data" / "DT_Monster.uasset").write_bytes(b"DT-v2")  # 그 사이 다시 저장됨
        return {"ok": True, "meta": {p: tbl for p in packages}}

    saved, skipped = metamod.capture_meta(st, [entry], fn)
    assert (saved, skipped) == (0, 1)
    assert not metamod.has_meta(st, entry.sha)


def test_capture_meta_survives_dead_bridge(st) -> None:
    entry = [e for e in st._work_tree().values()][0]
    entry.cls = "DataTable"

    def boom(*a, **kw):
        raise TimeoutError("브릿지 없음")

    assert metamod.capture_meta(st, [entry], boom) == (0, 1)
    assert metamod.capture_for_snapshot(st, st.status(), boom) == (0, 0)  # cls 가 ? 라 대상 없음


# ---- api_metadiff ----
def test_api_metadiff(st) -> None:
    a_sha, b_sha = "aa" * 32, "bb" * 32
    metamod.save_meta(st, a_sha, _tbl({"Goblin": {"Atk": "30", "Hp": "100"}}))
    metamod.save_meta(st, b_sha, _tbl({"Goblin": {"Atk": "45", "Hp": "100"}}))
    r = web.api_metadiff(st, a_sha, b_sha)
    assert r["available"] and r["kind"] == "DataTable"
    assert r["diff"]["rows_changed"][0]["cells"][0]["new"] == "45"

    miss = web.api_metadiff(st, "cc" * 32, b_sha)
    assert miss["available"] is False and miss["missing"] == ["a"]

    fresh = web.api_metadiff(st, "", b_sha)
    assert fresh["available"] and [x["row"] for x in fresh["diff"]["rows_added"]] == ["Goblin"]

    with pytest.raises(ValueError):
        web.api_metadiff(st, "zz;rm", b_sha)


# ---- gc ----
def test_gc_removes_orphan_meta(st) -> None:
    snap, _, _ = st.snap("first")
    live = list(st.tree(snap.id).values())[0].sha
    metamod.save_meta(st, live, _tbl({"Goblin": {"Atk": "30", "Hp": "100"}}))
    orphan = "ff" * 32
    metamod.save_meta(st, orphan, _tbl({"Orc": {"Atk": "1", "Hp": "2"}}))
    r = st.gc()
    assert tuple(r)[0] >= 0 and r.meta == 1
    assert metamod.has_meta(st, live) and not metamod.has_meta(st, orphan)
