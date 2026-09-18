"""UE diff 열기 단위 테스트. 실제 UnrealEditor 는 절대 실행하지 않는다(가짜 launcher)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jokate import config as cfgmod  # noqa: E402
from jokate import store as storemod  # noqa: E402
from jokate import uediff  # noqa: E402
from jokate import web  # noqa: E402


@pytest.fixture
def st(tmp_path: Path) -> storemod.Store:
    root = tmp_path / "Proj"
    foo = root / "Content" / "Foo"
    foo.mkdir(parents=True)
    cfgmod.init(root)
    (root / "Proj.uproject").write_text('{"EngineAssociation": "5.7"}', encoding="utf-8")
    (foo / "A.uasset").write_bytes(b"AAAA-v1")
    s = storemod.Store(cfgmod.load(root))
    s.snap("first")
    (foo / "A.uasset").write_bytes(b"AAAA-v2-longer")
    s.snap("second")
    return s


def fake_exe(tmp_path: Path, engine: str = "UE_5.7") -> Path:
    p = tmp_path / engine / "Engine/Binaries/Win64/UnrealEditor.exe"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("exe", encoding="utf-8")
    return p


def shas(st: storemod.Store, rel: str = "Foo/A.uasset") -> list[str]:
    return [st.tree(1)[rel].sha, st.tree(2)[rel].sha]


# ---- find_editor_exe ----
def test_find_editor_exe_version(st: storemod.Store, tmp_path: Path) -> None:
    exe = fake_exe(tmp_path)
    calls = []

    def reg(hive, key, name):
        calls.append((hive, key, name))
        return str(exe.parents[3])

    assert uediff.find_editor_exe(st.cfg, reg) == exe
    assert calls[0][0] == "HKLM" and calls[0][1].endswith("5.7") and calls[0][2] == "InstalledDirectory"


def test_find_editor_exe_guid(st: storemod.Store, tmp_path: Path) -> None:
    guid = "{2E1C4A3B-0000-0000-0000-000000000001}"
    (st.cfg.root / "Proj.uproject").write_text('{"EngineAssociation": "%s"}' % guid, encoding="utf-8")
    exe = fake_exe(tmp_path, "SrcBuild")
    calls = []

    def reg(hive, key, name):
        calls.append((hive, key, name))
        return str(exe.parents[3])

    assert uediff.find_editor_exe(st.cfg, reg) == exe
    assert calls[0][0] == "HKCU" and calls[0][1].endswith("Builds") and calls[0][2] == guid


def test_find_editor_exe_config_wins(st: storemod.Store, tmp_path: Path) -> None:
    exe = fake_exe(tmp_path, "Custom")
    st.cfg.editor_exe = str(exe)

    def reg(hive, key, name):
        raise AssertionError("config 가 있으면 레지스트리를 보지 않는다")

    assert uediff.find_editor_exe(st.cfg, reg) == exe


def test_find_editor_exe_missing(st: storemod.Store, tmp_path: Path) -> None:
    assert uediff.find_editor_exe(st.cfg, lambda *a: str(tmp_path / "없는엔진")) is None
    assert uediff.find_editor_exe(st.cfg, lambda *a: None) is None
    st.cfg.editor_exe = str(tmp_path / "없다/UnrealEditor.exe")
    assert uediff.find_editor_exe(st.cfg, lambda *a: None) is None


def test_config_reads_editor_exe(tmp_path: Path) -> None:
    root = tmp_path / "P"
    (root / ".jokate").mkdir(parents=True)
    (root / ".jokate" / "config.toml").write_text('[editor]\nexe = "C:/UE/UnrealEditor.exe"\n', encoding="utf-8")
    assert cfgmod.load(root).editor_exe == "C:/UE/UnrealEditor.exe"
    assert cfgmod.load(tmp_path).editor_exe == ""


# ---- extract_version / build_command ----
def test_extract_version_reuses(st: storemod.Store) -> None:
    a, _b = shas(st)
    p = uediff.extract_version(st, "Foo/A.uasset", a)
    assert p.exists() and p.name == f"A__{a[:8]}.uasset"
    assert p.read_bytes() == b"AAAA-v1"
    mtime = p.stat().st_mtime_ns
    assert uediff.extract_version(st, "Foo/A.uasset", a) == p
    assert p.stat().st_mtime_ns == mtime           # 재사용 (다시 복사 안 함)
    with pytest.raises(ValueError):
        uediff.extract_version(st, "Foo/A.uasset", "nothex!")
    with pytest.raises(KeyError):
        uediff.extract_version(st, "Foo/A.uasset", "deadbeef" * 5)


def test_cleanup_tmp(st: storemod.Store) -> None:
    a, _ = shas(st)
    p = uediff.extract_version(st, "Foo/A.uasset", a)
    assert uediff.cleanup_tmp(st) == 0 and p.exists()
    import os
    old = p.stat().st_mtime - 60 * 60 * 48
    os.utime(p, (old, old))
    assert uediff.cleanup_tmp(st, max_age_hours=24) == 1 and not p.exists()


def test_build_command() -> None:
    assert uediff.build_command("E.exe", "P.uproject", "L.uasset", "R.uasset") == \
        ["E.exe", "P.uproject", "-diff", "L.uasset", "R.uasset"]


# ---- open_diff ----
def test_open_diff_two_versions(st: storemod.Store, tmp_path: Path) -> None:
    exe = fake_exe(tmp_path)
    st.cfg.editor_exe = str(exe)
    a, b = shas(st)
    seen = []
    r = uediff.open_diff(st, "Foo/A.uasset", a, b, launcher=lambda cmd: seen.append(cmd) or 4242)
    assert r["pid"] == 4242
    cmd = seen[0]
    assert cmd[0] == str(exe) and cmd[1].endswith(".uproject") and cmd[2] == "-diff"
    assert Path(cmd[3]).read_bytes() == b"AAAA-v1" and Path(cmd[4]).read_bytes() == b"AAAA-v2-longer"


def test_open_diff_against_worktree(st: storemod.Store, tmp_path: Path) -> None:
    st.cfg.editor_exe = str(fake_exe(tmp_path))
    a, _ = shas(st)
    seen = []
    r = uediff.open_diff(st, "Foo/A.uasset", a, None, launcher=lambda cmd: seen.append(cmd) or 7)
    assert Path(r["right"]) == (st.cfg.content / "Foo/A.uasset")
    assert seen[0][4] == str(st.cfg.content / "Foo/A.uasset")
    (st.cfg.content / "Foo/A.uasset").unlink()
    with pytest.raises(ValueError):
        uediff.open_diff(st, "Foo/A.uasset", a, None, launcher=lambda cmd: 0)


def test_open_diff_no_editor(st: storemod.Store, tmp_path: Path) -> None:
    st.cfg.editor_exe = str(tmp_path / "없는곳/UnrealEditor.exe")   # 실제 설치된 UE 를 타지 않게
    a, b = shas(st)
    with pytest.raises(uediff.EditorNotFound) as ei:
        uediff.open_diff(st, "Foo/A.uasset", a, b, launcher=lambda cmd: 0)
    assert "[editor] exe" in str(ei.value)


# ---- web API ----
def test_api_uediff(st: storemod.Store, tmp_path: Path) -> None:
    st.cfg.editor_exe = str(fake_exe(tmp_path))
    a, b = shas(st)
    r = web.api_uediff(st, "Foo/A.uasset", a, b, launcher=lambda cmd: 99)
    assert r["ok"] is True and r["pid"] == 99 and "/" in r["left"]


def test_api_uediff_editor_not_found_409(st: storemod.Store, tmp_path: Path) -> None:
    st.cfg.editor_exe = str(tmp_path / "없는곳/UnrealEditor.exe")
    a, b = shas(st)
    try:
        web.api_uediff(st, "Foo/A.uasset", a, b, launcher=lambda cmd: 0)
        raise AssertionError("EditorNotFound 가 나야 한다")
    except uediff.EditorNotFound as e:
        code, payload = web.error_response(e)
    assert code == 409 and payload["ok"] is False and "exe" in payload["error"]


def test_api_uediff_bad_sha_and_missing_object(st: storemod.Store, tmp_path: Path) -> None:
    st.cfg.editor_exe = str(fake_exe(tmp_path))
    a, _ = shas(st)
    code, _ = web.error_response(pytest.raises(ValueError, web.api_uediff, st, "Foo/A.uasset", "zz", None).value)
    assert code == 400
    with pytest.raises(KeyError) as ei:
        web.api_uediff(st, "Foo/A.uasset", "ab" * 20, None, launcher=lambda cmd: 0)
    assert web.error_response(ei.value)[0] == 404
    assert a


# ---- 에디터 안에서 열기 (in-editor) ----
class FakeBridge:
    """jokate.bridge 대역 — 실제 에디터 없이 op 'diff' 왕복을 흉내낸다."""

    def __init__(self, alive=True, resp=None, exc=None):
        self.alive = alive
        self.resp = resp or {"ok": True, "strategy": "file"}
        self.exc = exc
        self.calls = []

    def bridge_alive(self, cfg, *a, **k):
        return self.alive

    def request(self, cfg, op, packages, args=None, timeout=30.0, poll=0.2):
        self.calls.append((op, dict(args or {}), timeout))
        if self.exc is not None:
            raise self.exc
        return self.resp


def test_open_diff_in_editor(st: storemod.Store, tmp_path: Path) -> None:
    st.cfg.editor_exe = str(fake_exe(tmp_path))
    a, _ = shas(st)
    br = FakeBridge()
    r = uediff.open_diff(st, "Foo/A.uasset", a, None,
                         launcher=lambda cmd: pytest.fail("프로세스를 띄우면 안 된다"),
                         bridge=br, editor_running=lambda: True)
    assert r["mode"] == "editor" and r["strategy"] == "file" and r["pid"] is None
    op, args, timeout = br.calls[0]
    assert op == "diff" and timeout == uediff.DIFF_TIMEOUT
    assert args["left_label"] == a[:8] and args["right_label"] == "현재"
    assert args["right_package"] == "/Game/Foo/A"
    saved = st.cfg.root / "Saved" / "JokateDiff" / a[:8] / "A.uasset"     # 전략 1 용
    content = st.cfg.content / "_JokateDiff" / a[:8] / "A.uasset"          # 전략 2 용
    assert saved.read_bytes() == b"AAAA-v1" and content.read_bytes() == b"AAAA-v1"
    assert args["left_file"] == saved.as_posix()
    assert args["left_package"] == "/Game/_JokateDiff/%s/A" % a[:8]


def test_open_diff_in_editor_two_versions(st: storemod.Store, tmp_path: Path) -> None:
    st.cfg.editor_exe = str(fake_exe(tmp_path))
    a, b = shas(st)
    br = FakeBridge(resp={"ok": True, "strategy": "package"})
    r = uediff.open_diff(st, "Foo/A.uasset", a, b, launcher=lambda cmd: 0, bridge=br,
                         editor_running=lambda: True)
    assert r["mode"] == "editor" and r["strategy"] == "package"
    args = br.calls[0][1]
    assert args["right_package"] == "/Game/_JokateDiff/%s/A" % b[:8]
    assert (st.cfg.content / "_JokateDiff" / b[:8] / "A.uasset").read_bytes() == b"AAAA-v2-longer"


def test_open_diff_falls_back_when_bridge_fails(st: storemod.Store, tmp_path: Path) -> None:
    st.cfg.editor_exe = str(fake_exe(tmp_path))
    a, b = shas(st)
    seen = []
    br = FakeBridge(resp={"ok": False, "error": "로드 실패"})
    r = uediff.open_diff(st, "Foo/A.uasset", a, b, launcher=lambda cmd: seen.append(cmd) or 11,
                         bridge=br, editor_running=lambda: True)
    assert r["mode"] == "process" and r["pid"] == 11 and "로드 실패" in r["note"]
    assert seen and seen[0][2] == "-diff"


def test_open_diff_no_bridge_is_process(st: storemod.Store, tmp_path: Path) -> None:
    st.cfg.editor_exe = str(fake_exe(tmp_path))
    a, b = shas(st)
    br = FakeBridge(alive=False)
    r = uediff.open_diff(st, "Foo/A.uasset", a, b, launcher=lambda cmd: 5, bridge=br,
                         editor_running=lambda: False)
    assert r["mode"] == "process" and r["pid"] == 5 and br.calls == [] and r["note"]


# ---- _JokateDiff 는 절대 추적하지 않는다 ----
def test_jokatediff_never_tracked(st: storemod.Store) -> None:
    from jokate import watch as watchmod
    assert st.cfg.tier_of(Path("_JokateDiff/abc12345/A.uasset")) is None
    st.cfg.vendor = ["*"]          # 설정과 무관하게 무시
    st.cfg.ignore = []
    assert st.cfg.tier_of(Path("_JokateDiff/abc12345/A.uasset")) is None
    d = st.cfg.content / "_JokateDiff" / "abc12345"
    d.mkdir(parents=True)
    (d / "A.uasset").write_bytes(b"AAAA-v1")
    assert not any(r.startswith("_JokateDiff/") for r in watchmod.fingerprint(st.cfg))
    assert not any(r.rel.startswith("_JokateDiff/") for r in st.scan_authored())


def test_cleanup_tmp_removes_editor_dirs(st: storemod.Store) -> None:
    import os
    a, _ = shas(st)
    uediff.extract_for_editor(st, "Foo/A.uasset", a)
    saved = uediff.editor_tmp_dir(st.cfg) / a[:8]
    content = uediff.content_diff_dir(st.cfg) / a[:8]
    assert saved.is_dir() and content.is_dir()
    assert uediff.cleanup_tmp(st) == 0 and saved.is_dir()
    old = saved.stat().st_mtime - 60 * 60 * 48
    for d in (saved, content):
        os.utime(d, (old, old))
    assert uediff.cleanup_tmp(st, max_age_hours=24) == 2
    assert not saved.exists() and not content.exists()


def test_api_uediff_reports_mode(st: storemod.Store, tmp_path: Path) -> None:
    st.cfg.editor_exe = str(fake_exe(tmp_path))
    a, b = shas(st)
    r = web.api_uediff(st, "Foo/A.uasset", a, b, launcher=lambda cmd: 3)
    assert r["mode"] == "process" and "note" in r and "strategy" in r
