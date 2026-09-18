"""
스냅샷 저장소

- 객체: <project>/.jokate/store/objects/<sha[:2]>/<sha>  (원본 그대로, 압축 없음, 내용주소)
- 인덱스: <project>/.jokate/index.sqlite
    snapshots(id, parent, kind auto|label, message, ts)
    tree(snapshot_id, rel, sha, size, cls, deps)
- authored 등급만 대상. 변경 판단은 mtime 이 아니라 sha 비교.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .config import ASSET_EXTS, Config
from .scan import AssetRecord, _scan_one

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    parent  INTEGER,
    kind    TEXT NOT NULL CHECK(kind IN ('auto','label')),
    message TEXT NOT NULL DEFAULT '',
    ts      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tree(
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    rel  TEXT NOT NULL,
    sha  TEXT NOT NULL,
    size INTEGER NOT NULL,
    cls  TEXT NOT NULL DEFAULT '',
    deps TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(snapshot_id, rel)
);
CREATE INDEX IF NOT EXISTS tree_sha ON tree(sha);
"""


@dataclass
class TreeEntry:
    rel: str
    sha: str
    size: int
    cls: str = ""
    deps: list[str] = field(default_factory=list)


@dataclass
class Snapshot:
    id: int
    parent: int | None
    kind: str
    message: str
    ts: float


@dataclass
class Diff:
    added: list[TreeEntry] = field(default_factory=list)
    modified: list[tuple[TreeEntry, TreeEntry]] = field(default_factory=list)   # (old, new)
    deleted: list[TreeEntry] = field(default_factory=list)
    moved: list[tuple[TreeEntry, TreeEntry]] = field(default_factory=list)      # (old, new) sha 동일·경로 변경

    @property
    def empty(self) -> bool:
        return not (self.added or self.modified or self.deleted or self.moved)

    def by_class(self) -> dict[str, Counter]:
        out: dict[str, Counter] = {}
        for kind, items in (("added", self.added), ("deleted", self.deleted)):
            for e in items:
                out.setdefault(e.cls or "?", Counter())[kind] += 1
        for kind, items in (("modified", self.modified), ("moved", self.moved)):
            for _, new in items:
                out.setdefault(new.cls or "?", Counter())[kind] += 1
        return out


class Store:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.objects = cfg.state_dir / "store" / "objects"
        self.db_path = cfg.state_dir / "index.sqlite"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path)
        self.db.executescript(SCHEMA)

    # ---- objects ----
    def object_path(self, sha: str) -> Path:
        return self.objects / sha[:2] / sha

    def put_object(self, src: Path, sha: str) -> bool:
        """이미 있으면 건너뜀. 새로 저장하면 True."""
        dst = self.object_path(sha)
        if dst.exists():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".tmp")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
        return True

    # ---- working tree ----
    def scan_authored(self, workers: int = 8) -> list[AssetRecord]:
        jobs: list[Path] = []
        for p in self.cfg.content.rglob("*"):
            if p.suffix.lower() not in ASSET_EXTS:
                continue
            if self.cfg.tier_of(p.relative_to(self.cfg.content)) != "authored":
                continue
            jobs.append(p)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            recs = list(ex.map(lambda p: _scan_one(self.cfg, p, "authored", do_hash=True), jobs))
        recs.sort(key=lambda r: r.rel)
        return recs

    # ---- snapshots ----
    def head(self) -> Snapshot | None:
        row = self.db.execute("SELECT id,parent,kind,message,ts FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return Snapshot(*row) if row else None

    def get(self, sid: int) -> Snapshot | None:
        row = self.db.execute("SELECT id,parent,kind,message,ts FROM snapshots WHERE id=?", (sid,)).fetchone()
        return Snapshot(*row) if row else None

    def log(self) -> list[Snapshot]:
        rows = self.db.execute("SELECT id,parent,kind,message,ts FROM snapshots ORDER BY id DESC").fetchall()
        return [Snapshot(*r) for r in rows]

    def tree(self, sid: int | None) -> dict[str, TreeEntry]:
        if sid is None:
            return {}
        rows = self.db.execute("SELECT rel,sha,size,cls,deps FROM tree WHERE snapshot_id=?", (sid,)).fetchall()
        return {r[0]: TreeEntry(r[0], r[1], r[2], r[3], json.loads(r[4])) for r in rows}

    def _work_tree(self) -> dict[str, TreeEntry]:
        return {r.rel: TreeEntry(r.rel, r.sha, r.size, r.cls, r.deps) for r in self.scan_authored()}

    def status(self) -> Diff:
        """HEAD 트리 → 작업 트리(디스크) Diff. 아직 스냅샷에 올리지 않은 변경."""
        parent = self.head()
        return diff_trees(self.tree(parent.id if parent else None), self._work_tree())

    def snap(self, message: str = "", *, kind: str | None = None,
             force: bool = False, only: list[str] | None = None) -> tuple[Snapshot | None, Diff, int]:
        """작업 트리를 스냅샷으로 저장. 반환 (snapshot|None(변경 없음), diff, 새 객체 수).

        only 가 있으면 부분 스냅샷: HEAD 트리 복사본에 only 의 rel 만 디스크 상태로 갱신(디스크에 없으면 제거),
        나머지는 HEAD 그대로. only 의 rel 이 HEAD 에도 디스크에도 없으면 KeyError.
        """
        if kind is None:
            kind = "label" if message else "auto"
        work = self._work_tree()
        parent = self.head()
        old_tree = self.tree(parent.id if parent else None)
        if only:
            new_tree = dict(old_tree)
            to_store: list[TreeEntry] = []
            for a in only:
                rel = a.replace("\\", "/").strip("/")
                if rel not in work and rel not in old_tree:
                    raise KeyError(f"{rel}: HEAD 에도 디스크에도 없음")
                if rel in work:
                    new_tree[rel] = work[rel]
                    to_store.append(work[rel])
                else:
                    new_tree.pop(rel, None)
        else:
            new_tree = work
            to_store = list(work.values())
        d = diff_trees(old_tree, new_tree)
        if d.empty and parent is not None and not force:
            return None, d, 0
        stored = 0
        for e in to_store:
            if self.put_object(self.cfg.content / e.rel, e.sha):
                stored += 1
        cur = self.db.cursor()
        cur.execute("INSERT INTO snapshots(parent,kind,message,ts) VALUES(?,?,?,?)",
                    (parent.id if parent else None, kind, message, time.time()))
        sid = cur.lastrowid
        cur.executemany("INSERT INTO tree(snapshot_id,rel,sha,size,cls,deps) VALUES(?,?,?,?,?,?)",
                        [(sid, e.rel, e.sha, e.size, e.cls, json.dumps(e.deps)) for e in new_tree.values()])
        self.db.commit()
        return self.get(sid), d, stored

    def show(self, sid: int) -> tuple[Snapshot, Diff]:
        s = self.get(sid)
        if s is None:
            raise KeyError(f"snapshot {sid} 없음")
        return s, diff_trees(self.tree(s.parent), self.tree(s.id))

    # ---- restore ----
    def plan_restore(self, sid: int, assets: list[str] | None = None) -> "RestorePlan":
        """스냅샷 <sid> 로 되돌릴 계획(드라이런). assets 가 있으면 그 rel 들만 대상, 나머지는 현재 유지."""
        s = self.get(sid)
        if s is None:
            raise KeyError(f"snapshot {sid} 없음")
        target = self.tree(sid)
        recs = self.scan_authored()
        current = {r.rel: TreeEntry(r.rel, r.sha, r.size, r.cls, r.deps) for r in recs}
        if assets:
            result = dict(current)
            for a in assets:
                rel = a.replace("\\", "/").strip("/")
                if rel not in target and rel not in current:
                    raise KeyError(f"{rel}: 스냅샷 #{sid} 에도 현재 트리에도 없음")
                if rel in target:
                    result[rel] = target[rel]
                else:
                    result.pop(rel, None)
        else:
            result = dict(target)
        d = diff_trees(current, result)
        plan = RestorePlan(snapshot=s, current=current, result=result, diff=d)
        self._check_refs(plan)
        return plan

    def _check_refs(self, plan: "RestorePlan") -> None:
        """결과 트리 참조 검산. /Game/ 의존성이 결과 트리·vendor·현재 디스크 어디에도 없으면 경고."""
        vanishing_rels = {e.rel for e in plan.diff.deleted} | {o.rel for o, _ in plan.diff.moved}
        result_pkgs = {_pkg_of(rel): rel for rel in plan.result}
        vanishing_pkgs = {_pkg_of(rel) for rel in vanishing_rels}
        cache: dict[str, bool] = {}

        def resolvable(pkg: str) -> bool:
            if pkg in cache:
                return cache[pkg]
            ok = pkg in result_pkgs
            if not ok:
                sub = pkg[len("/Game/"):]
                for ext in ASSET_EXTS:
                    p = self.cfg.content / (sub + ext)
                    if not p.exists():
                        continue
                    rel = p.relative_to(self.cfg.content)
                    tier = self.cfg.tier_of(rel)
                    if tier == "vendor" or (tier == "authored" and rel.as_posix() not in vanishing_rels):
                        ok = True
                        break
            cache[pkg] = ok
            return ok

        for rel in sorted(plan.result):
            e = plan.result[rel]
            for dep in e.deps:
                if not dep.startswith("/Game/"):
                    continue
                if dep in vanishing_pkgs and dep not in result_pkgs:
                    plan.dependents.append((rel, dep))
                elif not resolvable(dep):
                    plan.broken.append((rel, dep))

    def apply_restore(self, plan: "RestorePlan", *, check_editor: bool = True,
                      discard_dirty: bool = False) -> "RestoreResult":
        """계획 적용: 안전 스냅샷 → 파일 복사/삭제 → 결과 스냅샷.

        에디터 실행 중이면 브릿지(jokate.bridge)가 살아 있어야 하고, 대상 패키지가 dirty 면 중단
        (discard_dirty=True 면 통과). 파일 적용 후 에디터에 reload 요청.
        """
        use_bridge = False
        if check_editor and editor_running():
            from . import bridge
            if not bridge.bridge_alive(self.cfg):
                raise RestoreBlocked("에디터가 켜져 있는데 브릿지가 없다 — 에디터 Python 콘솔에서 "
                                     "import jokate_bridge 실행 또는 에디터 종료")
            use_bridge = True
        sid = plan.snapshot.id
        # 객체가 모두 있는지 먼저 확인
        for e in plan.result.values():
            if not self.object_path(e.sha).exists():
                raise FileNotFoundError(f"객체 없음: {e.rel} ({e.sha[:12]})")
        to_write = [rel for rel, e in plan.result.items()
                    if plan.current.get(rel) is None or plan.current[rel].sha != e.sha]
        to_delete = [rel for rel in plan.current if rel not in plan.result]
        pkgs = sorted({_pkg_of(rel) for rel in to_write + to_delete})
        if use_bridge and pkgs:
            try:
                r = bridge.request(self.cfg, "dirty", pkgs)
            except TimeoutError as e:
                raise RestoreBlocked(str(e)) from e
            if not r.get("ok"):
                raise RestoreBlocked(f"에디터 dirty 확인 실패: {r.get('error')}")
            dirty = list(r.get("dirty") or [])
            if dirty and not discard_dirty:
                raise RestoreBlocked("에디터에 저장 안 된 패키지가 있다 (저장하거나 --discard-dirty):\n  "
                                     + "\n  ".join(dirty), dirty=dirty)
        safety, _, _ = self.snap(f"롤백 직전 #{sid}", kind="auto", force=True)
        written = 0
        for rel in to_write:
            e = plan.result[rel]
            dst = self.cfg.content / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".jokate-tmp")
            shutil.copyfile(self.object_path(e.sha), tmp)
            tmp.replace(dst)
            written += 1
        deleted = 0
        for rel in to_delete:
            p = self.cfg.content / rel
            if p.exists():
                p.unlink()
                deleted += 1
        reloaded: int | None = None
        if use_bridge and pkgs:
            try:
                r = bridge.request(self.cfg, "reload", pkgs, {"discard_dirty": discard_dirty})
            except TimeoutError as e:
                raise RuntimeError(f"파일은 적용됐지만 {e} — 에디터를 재시작하라") from e
            if not r.get("ok"):
                raise RuntimeError(f"파일은 적용됐지만 에디터 reload 실패: {r.get('error')} "
                                   f"dirty={r.get('dirty') or []} — 에디터를 재시작하라")
            reloaded = int(r.get("reloaded") or 0)
        result, _, _ = self.snap(f"롤백: #{sid}", kind="label", force=True)
        return RestoreResult(safety=safety, result=result, written=written, deleted=deleted, reloaded=reloaded)

    def close(self) -> None:
        self.db.close()


class RestoreBlocked(RuntimeError):
    """적용 전 중단(파일 변경 없음): 브릿지 없음·dirty 확인 실패·저장 안 된 패키지. dirty 에 패키지 목록."""

    def __init__(self, msg: str, dirty: list[str] | None = None):
        super().__init__(msg)
        self.dirty: list[str] = list(dirty or [])


@dataclass
class RestorePlan:
    snapshot: Snapshot
    current: dict[str, TreeEntry]
    result: dict[str, TreeEntry]
    diff: Diff                                   # 현재 → 결과 (added=부활, modified=수정 되돌림, deleted=삭제, moved=이동)
    broken: list[tuple[str, str]] = field(default_factory=list)      # (rel, dep) 결과 트리·vendor·디스크 어디에도 없는 참조
    dependents: list[tuple[str, str]] = field(default_factory=list)  # (rel, dep) 롤백으로 사라지는 애셋을 참조


@dataclass
class RestoreResult:
    safety: Snapshot
    result: Snapshot
    written: int
    deleted: int
    reloaded: int | None = None   # 브릿지로 에디터에 reload 한 패키지 수 (에디터 안 켜져 있으면 None)


def _pkg_of(rel: str) -> str:
    return "/Game/" + rel.rsplit(".", 1)[0]


def editor_running() -> bool:
    """tasklist 에 UnrealEditor.exe 가 있으면 True. Windows 가 아니면 False."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq UnrealEditor.exe", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return False
    return "unrealeditor.exe" in out.lower()


def format_restore(plan: RestorePlan) -> str:
    s = plan.snapshot
    lines = [f"restore → snapshot #{s.id} ({s.kind}) {format_ts(s.ts)}  {s.message}".rstrip()]
    d = plan.diff
    for o, n in d.modified:
        lines.append(f"  M {n.rel}  [{n.cls or '?'}]  {o.size}→{n.size}B  (수정 되돌림)")
    for e in d.added:
        lines.append(f"  A {e.rel}  [{e.cls or '?'}]  (부활)")
    for o, n in d.moved:
        lines.append(f"  R {o.rel} → {n.rel}  [{n.cls or '?'}]  (이동)")
    for e in d.deleted:
        lines.append(f"  D {e.rel}  [{e.cls or '?'}]  (삭제)")
    if d.empty:
        lines.append("  (변경 없음 — 이미 해당 상태)")
    bc = d.by_class()
    if bc:
        lines.append("클래스별:")
        for cls in sorted(bc):
            c = bc[cls]
            parts = [f"{k} {c[k]}" for k in ("modified", "added", "moved", "deleted") if c[k]]
            lines.append(f"  {cls:<24} " + ", ".join(parts))
    if plan.dependents:
        lines.append(f"경고: 롤백으로 사라지는 애셋을 참조 ({len(plan.dependents)}):")
        for rel, dep in plan.dependents:
            lines.append(f"  ! {rel} → {dep}")
    if plan.broken:
        lines.append(f"경고: 깨질 참조 ({len(plan.broken)}):")
        for rel, dep in plan.broken:
            lines.append(f"  ! {rel} → {dep}")
    if not plan.dependents and not plan.broken:
        lines.append("참조 검산: 이상 없음")
    return "\n".join(lines)


def diff_trees(old: dict[str, TreeEntry], new: dict[str, TreeEntry]) -> Diff:
    d = Diff()
    gone = [e for rel, e in old.items() if rel not in new]
    came = [e for rel, e in new.items() if rel not in old]
    for rel, e in new.items():
        o = old.get(rel)
        if o is not None and o.sha != e.sha:
            d.modified.append((o, e))
    # 이동: sha 동일 + 경로 변경 (삭제·추가 쌍에서 매칭)
    gone_by_sha: dict[str, list[TreeEntry]] = {}
    for e in gone:
        gone_by_sha.setdefault(e.sha, []).append(e)
    for e in came:
        cands = gone_by_sha.get(e.sha)
        if cands:
            d.moved.append((cands.pop(0), e))
        else:
            d.added.append(e)
    d.deleted = [e for lst in gone_by_sha.values() for e in lst]
    for lst in (d.added, d.deleted):
        lst.sort(key=lambda e: e.rel)
    for lst in (d.modified, d.moved):
        lst.sort(key=lambda t: t[1].rel)
    return d


def format_diff(d: Diff) -> str:
    lines = []
    for e in d.added:
        lines.append(f"  A {e.rel}  [{e.cls or '?'}]")
    for o, n in d.modified:
        lines.append(f"  M {n.rel}  [{n.cls or '?'}]  {o.size}→{n.size}B")
    for o, n in d.moved:
        lines.append(f"  R {o.rel} → {n.rel}  [{n.cls or '?'}]")
    for e in d.deleted:
        lines.append(f"  D {e.rel}  [{e.cls or '?'}]")
    if not lines:
        lines.append("  (변경 없음)")
    bc = d.by_class()
    if bc:
        lines.append("클래스별:")
        for cls in sorted(bc):
            c = bc[cls]
            parts = [f"{k} {c[k]}" for k in ("added", "modified", "moved", "deleted") if c[k]]
            lines.append(f"  {cls:<24} " + ", ".join(parts))
    return "\n".join(lines)


def format_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
