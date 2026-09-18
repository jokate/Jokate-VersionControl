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

    def snap(self, message: str = "", *, kind: str | None = None,
             force: bool = False) -> tuple[Snapshot | None, Diff, int]:
        """작업 트리를 스냅샷으로 저장. 반환 (snapshot|None(변경 없음), diff, 새 객체 수)."""
        if kind is None:
            kind = "label" if message else "auto"
        recs = self.scan_authored()
        new_tree = {r.rel: TreeEntry(r.rel, r.sha, r.size, r.cls, r.deps) for r in recs}
        parent = self.head()
        old_tree = self.tree(parent.id if parent else None)
        d = diff_trees(old_tree, new_tree)
        if d.empty and parent is not None and not force:
            return None, d, 0
        stored = 0
        for r in recs:
            if self.put_object(self.cfg.content / r.rel, r.sha):
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

    def close(self) -> None:
        self.db.close()


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
