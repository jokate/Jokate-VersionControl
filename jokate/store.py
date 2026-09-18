"""
스냅샷 저장소

- 객체: <project>/.jokate/store/objects/<sha[:2]>/<sha>  (원본 그대로, 압축 없음, 내용주소)
- 인덱스: <project>/.jokate/index.sqlite
    snapshots(id, parent, kind auto|label, message, ts)
    tree(snapshot_id, rel, sha, size, cls, deps, noise)
- authored 등급만 대상. 변경 판단은 mtime 이 아니라 sha 비교.
- noise: HEAD 대비 sha 는 바뀌었지만 export 직렬화 바이트는 동일(리세이브만)인 항목 표시.
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
from dataclasses import dataclass, field, replace
from pathlib import Path

from .config import ASSET_EXTS, Config
from .scan import AssetRecord, _scan_one
from .uasset import is_resave_only

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
    noise INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(snapshot_id, rel)
);
CREATE INDEX IF NOT EXISTS tree_sha ON tree(sha);
CREATE TABLE IF NOT EXISTS baseline(
    rel  TEXT PRIMARY KEY,
    sha  TEXT NOT NULL,
    size INTEGER NOT NULL,
    cls  TEXT NOT NULL DEFAULT '',
    deps TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS store_meta(
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL DEFAULT ''
);
"""


@dataclass
class TreeEntry:
    rel: str
    sha: str
    size: int
    cls: str = ""
    deps: list[str] = field(default_factory=list)
    noise: bool = False   # 직전 스냅샷 대비 리세이브만(헤더만 변경)


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

    @property
    def resave(self) -> list[tuple[TreeEntry, TreeEntry]]:
        """modified 중 리세이브만(noise)인 (old, new)."""
        return [(o, n) for o, n in self.modified if n.noise]

    @property
    def real_modified(self) -> list[tuple[TreeEntry, TreeEntry]]:
        return [(o, n) for o, n in self.modified if not n.noise]

    @property
    def all_noise(self) -> bool:
        """변경이 있고 그 전부가 리세이브만."""
        return (not self.empty) and not (self.added or self.deleted or self.moved or self.real_modified)

    def by_class(self) -> dict[str, Counter]:
        out: dict[str, Counter] = {}
        for kind, items in (("added", self.added), ("deleted", self.deleted)):
            for e in items:
                out.setdefault(e.cls or "?", Counter())[kind] += 1
        for _, new in self.modified:
            out.setdefault(new.cls or "?", Counter())["resave" if new.noise else "modified"] += 1
        for _, new in self.moved:
            out.setdefault(new.cls or "?", Counter())["moved"] += 1
        return out


class Store:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.objects = cfg.state_dir / "store" / "objects"
        self.db_path = cfg.state_dir / "index.sqlite"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path)
        self.db.executescript(SCHEMA)
        # 마이그레이션: 옛 스키마(noise 없음)면 컬럼 추가, 기존 행은 0
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(tree)").fetchall()}
        if "noise" not in cols:
            self.db.execute("ALTER TABLE tree ADD COLUMN noise INTEGER NOT NULL DEFAULT 0")
            self.db.commit()
        # 마이그레이션: snapshots.uploaded(이번에 사용자가 올린 항목 JSON)
        scols = {r[1] for r in self.db.execute("PRAGMA table_info(snapshots)").fetchall()}
        if "uploaded" not in scols:
            self.db.execute("ALTER TABLE snapshots ADD COLUMN uploaded TEXT NOT NULL DEFAULT ''")
            self.db.commit()
        self._init_baseline()

    # ---- baseline (사용자가 마지막으로 올린 상태) ----
    def _init_baseline(self) -> None:
        """옛 저장소: baseline 이 없으면 가장 최근 label 스냅샷(없으면 HEAD) 트리로 한 번 채운다."""
        done = self.db.execute("SELECT v FROM store_meta WHERE k='baseline_ready'").fetchone()
        n = self.db.execute("SELECT COUNT(*) FROM baseline").fetchone()[0]
        if done or n:
            return
        row = self.db.execute("SELECT id FROM snapshots WHERE kind='label' ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            row = self.db.execute("SELECT id FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if row is not None:
            self._write_baseline(self.tree(row[0]).values(), [])
        self.db.execute("INSERT OR REPLACE INTO store_meta(k,v) VALUES('baseline_ready','1')")
        self.db.commit()

    def baseline(self) -> dict[str, TreeEntry]:
        rows = self.db.execute("SELECT rel,sha,size,cls,deps FROM baseline").fetchall()
        return {r[0]: TreeEntry(r[0], r[1], r[2], r[3], json.loads(r[4])) for r in rows}

    def _write_baseline(self, put, drop) -> None:
        cur = self.db.cursor()
        cur.executemany("DELETE FROM baseline WHERE rel=?", [(r,) for r in drop])
        cur.executemany("INSERT OR REPLACE INTO baseline(rel,sha,size,cls,deps) VALUES(?,?,?,?,?)",
                        [(e.rel, e.sha, e.size, e.cls, json.dumps(e.deps)) for e in put])
        cur.execute("INSERT OR REPLACE INTO store_meta(k,v) VALUES('baseline_ready','1')")

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
        rows = self.db.execute("SELECT rel,sha,size,cls,deps,noise FROM tree WHERE snapshot_id=?", (sid,)).fetchall()
        return {r[0]: TreeEntry(r[0], r[1], r[2], r[3], json.loads(r[4]), bool(r[5])) for r in rows}

    def _mark_noise(self, old_tree: dict[str, TreeEntry], entries: list[TreeEntry],
                    *, from_objects: bool = False) -> None:
        """부모 트리 대비 sha 가 바뀐 항목마다 리세이브만인지 판정해 noise 표시(예외·파싱 실패는 False).

        from_objects=True 면 새 쪽도 작업 트리 파일이 아니라 store 객체를 읽는다(스냅샷 대 스냅샷).
        """
        for e in entries:
            o = old_tree.get(e.rel)
            if o is None or o.sha == e.sha:
                e.noise = False
                continue
            new_path = self.object_path(e.sha) if from_objects else self.cfg.content / e.rel
            try:
                e.noise = is_resave_only(self.object_path(o.sha), new_path)
            except Exception:
                e.noise = False

    def _work_tree(self) -> dict[str, TreeEntry]:
        return {r.rel: TreeEntry(r.rel, r.sha, r.size, r.cls, r.deps) for r in self.scan_authored()}

    def status(self) -> Diff:
        """baseline(마지막으로 올린 상태) → 작업 트리(디스크) Diff. 아직 올리지 않은 변경.

        자동 스냅샷(kind=auto)은 baseline 을 건드리지 않으므로 여기 결과를 비우지 않는다.
        """
        base = self.baseline()
        work = self._work_tree()
        self._mark_noise(base, list(work.values()))  # 올리기 전에도 리세이브만인지 보이게
        return diff_trees(base, work)

    def upload(self, message: str = "", only: list[str] | None = None) -> tuple[Snapshot | None, Diff, int]:
        """사용자가 '올리기': 고른 변경을 baseline 에 반영하고 label 스냅샷을 남긴다.

        스냅샷은 항상 '전체 작업 트리'로 찍고(HEAD 와 같아도 생성) uploaded 에 이번에 올린 항목만 기록한다.
        only 가 있으면 그 rel 들만(이동은 old/new 중 하나만 골라도 쌍으로). 올릴 게 없으면 (None, 빈 Diff, 0).
        반환 (snapshot|None, 이번에 올린 diff, 새 객체 수).
        """
        base = self.baseline()
        work = self._work_tree()
        self._mark_noise(base, list(work.values()))
        sel = _select_diff(diff_trees(base, work), only)
        if sel.empty:
            return None, sel, 0
        items = _uploaded_items(sel)
        parent = self.head()
        old_tree = self.tree(parent.id if parent else None)
        rows = [replace(e) for e in work.values()]      # 스냅샷 트리의 noise 는 직전 스냅샷 기준
        self._mark_noise(old_tree, rows)
        stored = 0
        for e in rows:
            if self.put_object(self.cfg.content / e.rel, e.sha):
                stored += 1
        cur = self.db.cursor()
        cur.execute("INSERT INTO snapshots(parent,kind,message,ts,uploaded) VALUES(?,?,?,?,?)",
                    (parent.id if parent else None, "label", message, time.time(), json.dumps(items)))
        sid = cur.lastrowid
        cur.executemany("INSERT INTO tree(snapshot_id,rel,sha,size,cls,deps,noise) VALUES(?,?,?,?,?,?,?)",
                        [(sid, e.rel, e.sha, e.size, e.cls, json.dumps(e.deps), int(e.noise)) for e in rows])
        put = [e for e in (*sel.added, *(n for _, n in sel.modified), *(n for _, n in sel.moved))]
        drop = [e.rel for e in sel.deleted] + [o.rel for o, _ in sel.moved]
        self._write_baseline(put, drop)
        self.db.commit()
        return self.get(sid), sel, stored

    def uploaded_diff(self, sid: int) -> Diff | None:
        """스냅샷에 기록된 '이번에 올린 것' → Diff. 기록이 없으면 None."""
        row = self.db.execute("SELECT uploaded FROM snapshots WHERE id=?", (sid,)).fetchone()
        if not row or not row[0]:
            return None
        return _diff_from_uploaded(json.loads(row[0]))

    def snap(self, message: str = "", *, kind: str | None = None,
             force: bool = False, only: list[str] | None = None) -> tuple[Snapshot | None, Diff, int]:
        """작업 트리를 스냅샷으로 저장. 반환 (snapshot|None(변경 없음), diff, 새 객체 수).

        kind 를 주지 않고 message 가 있으면 사용자 '올리기'(upload) 로 넘긴다(하위 호환).
        kind 를 직접 준 호출은 내부용: only 가 있으면 부분 스냅샷 — HEAD 트리 복사본에 only 의 rel 만
        디스크 상태로 갱신(디스크에 없으면 제거), 나머지는 HEAD 그대로. only 의 rel 이 HEAD 에도
        디스크에도 없으면 KeyError. baseline 은 건드리지 않는다.
        """
        if kind is None:
            if message:
                return self.upload(message, only=only)
            kind = "auto"
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
        self._mark_noise(old_tree, to_store)
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
        cur.executemany("INSERT INTO tree(snapshot_id,rel,sha,size,cls,deps,noise) VALUES(?,?,?,?,?,?,?)",
                        [(sid, e.rel, e.sha, e.size, e.cls, json.dumps(e.deps), int(e.noise))
                         for e in new_tree.values()])
        self.db.commit()
        return self.get(sid), d, stored

    def show(self, sid: int) -> tuple[Snapshot, Diff]:
        s = self.get(sid)
        if s is None:
            raise KeyError(f"snapshot {sid} 없음")
        up = self.uploaded_diff(sid)
        if up is not None:      # 사용자가 올린 스냅샷 → '이번에 올린 것'을 보여준다
            return s, up
        return s, diff_trees(self.tree(s.parent), self.tree(s.id))

    # ---- restore ----
    def plan_restore(self, sid: int, assets: list[str] | None = None) -> "RestorePlan":
        """스냅샷 <sid> 로 되돌릴 계획(드라이런). assets 가 있으면 그 rel 들만 대상, 나머지는 현재 유지."""
        s = self.get(sid)
        if s is None:
            raise KeyError(f"snapshot {sid} 없음")
        return self._plan_to_target(s, self.tree(sid), assets, f"스냅샷 #{sid}")

    def plan_revert_to_baseline(self, assets: list[str] | None = None) -> "RestorePlan":
        """지정한 애셋을 baseline(마지막으로 올린 상태)으로 되돌릴 계획. assets 가 없으면 전부."""
        pseudo = Snapshot(id=0, parent=None, kind="label", message="올린 상태(baseline)", ts=time.time())
        return self._plan_to_target(pseudo, self.baseline(), assets, "baseline")

    def revert_to_baseline(self, assets: list[str] | None = None, **kw) -> "RestoreResult":
        """계획 + 적용 한 번에 (apply_restore 의 안전 스냅샷·dirty 차단·reload 로직 그대로)."""
        return self.apply_restore(self.plan_revert_to_baseline(assets), **kw)

    def _plan_to_target(self, s: Snapshot, target_tree: dict[str, TreeEntry],
                        assets: list[str] | None, what: str) -> "RestorePlan":
        # noise 는 '직전 스냅샷 대비' 표시라 롤백 diff 에는 무의미 → 초기화
        target = {rel: replace(e, noise=False) for rel, e in target_tree.items()}
        recs = self.scan_authored()
        current = {r.rel: TreeEntry(r.rel, r.sha, r.size, r.cls, r.deps) for r in recs}
        if assets:
            result = dict(current)
            for a in assets:
                rel = a.replace("\\", "/").strip("/")
                if rel not in target and rel not in current:
                    raise KeyError(f"{rel}: {what} 에도 현재 트리에도 없음")
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
        """계획 적용: 안전 스냅샷(되돌릴 애셋만) → 파일 복사/삭제 → 결과 스냅샷(되돌릴 애셋만).

        두 스냅샷 모두 '이번 롤백이 건드리는 rel' 만 담는 부분 스냅샷이라, 롤백과 무관한 애셋의
        올리지 않은 변경은 롤백 뒤에도 '현재 변경사항' 으로 남는다.

        에디터 실행 중이면 브릿지(jokate.bridge)가 살아 있어야 하고, 대상 패키지가 dirty 면 중단
        (discard_dirty=True 면 통과). 파일 적용 후 에디터에 reload 요청.
        """
        use_bridge = False
        if check_editor and editor_running(self.cfg):
            from . import bridge
            if not bridge.bridge_alive(self.cfg):
                raise RestoreBlocked("에디터가 켜져 있는데 브릿지가 없다 — 에디터 Python 콘솔에서 "
                                     "import jokate_bridge 실행 또는 에디터 종료")
            use_bridge = True
        sid = plan.snapshot.id
        tag = f"#{sid}" if sid else "올린 상태"
        to_write = [rel for rel, e in plan.result.items()
                    if plan.current.get(rel) is None or plan.current[rel].sha != e.sha]
        # 실제로 쓸 항목의 객체만 확인한다 (현재 상태 그대로 유지되는 항목은
        # 아직 객체가 없을 수 있다 — 부분 롤백에서 수정만 하고 스냅샷 안 한 애셋)
        for rel in to_write:
            e = plan.result[rel]
            if not self.object_path(e.sha).exists():
                raise FileNotFoundError(f"객체 없음: {e.rel} ({e.sha[:12]})")
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
            # 에디터가 로드한 패키지는 .uasset 파일을 잠근다 → unload 로 잠금을 먼저 푼다.
            # 실패해도 여기서 막지 않는다: 바로 다음 사전 잠금 검사가 실제 상태로 판단한다.
            try:
                bridge.request(self.cfg, "release", pkgs)
            except (TimeoutError, OSError):
                pass
        # 사전 잠금 검사 (에디터가 꺼져 있어도 다른 프로그램이 잡고 있을 수 있다)
        locked = [rel for rel in to_write + to_delete if file_locked(self.cfg.content / rel)]
        if locked:
            raise RestoreBlocked("파일이 다른 프로그램에 잠겨 있어 되돌릴 수 없다 — 에디터에서 그 애셋의 "
                                 "편집 창을 닫고 다시 시도하라:\n  " + "\n  ".join(locked), locked=locked)
        # 이번 롤백이 실제로 건드리는 rel 만 스냅샷에 담는다 (무관한 애셋의 올리지 않은 변경은 그대로 둔다)
        affected = _affected_rels(plan.diff)
        head = self.head()
        head_tree = self.tree(head.id if head else None)
        work_now = self._work_tree()
        only_before = [r for r in affected if r in work_now or r in head_tree]
        safety = None
        if only_before:
            safety, _, _ = self.snap(f"롤백 직전 {tag}", kind="auto", only=only_before)
        safety_created = safety is not None
        if safety is None:   # 되돌릴 애셋의 디스크 상태가 HEAD 그대로 → HEAD 가 곧 '롤백 직전'
            safety = head if head is not None else self.head()
        written = 0
        deleted_files = 0
        tmps: list[Path] = []
        try:
            for rel in to_write:
                e = plan.result[rel]
                dst = self.cfg.content / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(dst.name + ".jokate-tmp")
                tmps.append(tmp)
                shutil.copyfile(self.object_path(e.sha), tmp)
                try:
                    _replace_with_retry(tmp, dst)
                except PermissionError as ex:
                    raise RestoreBlocked(_partial_msg(rel, written, safety), locked=[rel]) from ex
                written += 1
            for rel in to_delete:
                p = self.cfg.content / rel
                if p.exists():
                    try:
                        p.unlink()
                    except PermissionError as ex:
                        raise RestoreBlocked(_partial_msg(rel, written, safety), locked=[rel]) from ex
                    deleted_files += 1
        finally:
            for t in tmps:   # 이번 실행이 만든 잔여 tmp 는 성공·실패 상관없이 지운다
                try:
                    if t.exists():
                        t.unlink()
                except OSError:
                    pass
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
        head2 = self.head()
        tree2 = self.tree(head2.id if head2 else None)
        work2 = self._work_tree()
        only_after = [r for r in affected if r in work2 or r in tree2]
        result = None
        if only_after:
            result, _, _ = self.snap(f"롤백: {tag}", kind="label", only=only_after)
        if result is None:   # 이론상 없음 (바뀐 게 없으면 plan.diff 가 비었다)
            result = self.head()
        return RestoreResult(safety=safety, result=result, written=written, deleted=deleted_files,
                             reloaded=reloaded, safety_created=safety_created)

    # ---- 정리(보관기간·묶기·GC) ----
    def delete_snapshots(self, ids: list[int]) -> int:
        """스냅샷 여러 개 삭제(한 트랜잭션). HEAD 는 절대 지우지 않는다.

        지우는 스냅샷을 parent 로 가진 스냅샷은 (연쇄적으로) 살아남는 조상으로 다시 잇고,
        부모가 바뀐 스냅샷의 tree.noise 는 새 부모 기준으로 다시 계산한다. 반환: 지운 개수.
        """
        head = self.head()
        want = {int(i) for i in ids}
        if head is not None:
            want.discard(head.id)
        parents = {s.id: s.parent for s in self.log()}
        targets = sorted(i for i in want if i in parents)
        if not targets:
            return 0
        tset = set(targets)

        def survivor(p: int | None) -> int | None:
            seen: set[int] = set()
            while p is not None and p in tset and p not in seen:
                seen.add(p)
                p = parents.get(p)
            return p

        rewired = [(sid, survivor(par)) for sid, par in parents.items()
                   if sid not in tset and par is not None and par in tset]
        cur = self.db.cursor()
        try:
            for sid, newpar in rewired:
                cur.execute("UPDATE snapshots SET parent=? WHERE id=?", (newpar, sid))
            cur.executemany("DELETE FROM tree WHERE snapshot_id=?", [(i,) for i in targets])
            cur.executemany("DELETE FROM snapshots WHERE id=?", [(i,) for i in targets])
            for sid, newpar in rewired:
                old_tree = self.tree(newpar)
                entries = list(self.tree(sid).values())
                self._mark_noise(old_tree, entries, from_objects=True)
                cur.executemany("UPDATE tree SET noise=? WHERE snapshot_id=? AND rel=?",
                                [(int(e.noise), sid, e.rel) for e in entries])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return len(targets)

    def squash(self, ids: list[int], message: str = "",
               include_labels: bool = False) -> tuple[Snapshot, int]:
        """연속된 사슬(부모-자식)인 스냅샷들을 마지막 하나로 묶는다.

        마지막(가장 큰 id) 스냅샷만 kind=label + message 로 남기고 나머지는 삭제.
        사슬이 아니거나 2개 미만이면 ValueError. 반환 (남은 스냅샷, 지운 수).
        지워질 쪽에 이름 붙인(label) 스냅샷이 있으면 include_labels=False 일 때 SquashHasLabels.
        """
        want = sorted({int(i) for i in ids})
        if len(want) < 2:
            raise ValueError("묶으려면 스냅샷이 2개 이상이어야 한다")
        snaps = []
        for i in want:
            s = self.get(i)
            if s is None:
                raise ValueError(f"snapshot {i} 없음")
            snaps.append(s)
        for a, b in zip(snaps, snaps[1:]):
            if b.parent != a.id:
                raise ValueError(f"#{a.id} → #{b.id} 는 연속된 사슬이 아니다")
        keep = snaps[-1]
        if not include_labels:
            doomed = [(s.id, s.message) for s in snaps[:-1] if s.kind == "label"]
            if doomed:
                raise SquashHasLabels(
                    "묶으면 이름 붙인 스냅샷 "
                    + ", ".join(f"#{i} ({m})".rstrip() for i, m in doomed)
                    + " 가 함께 사라진다", doomed)
        merged = _merge_uploaded([self.db.execute("SELECT uploaded FROM snapshots WHERE id=?", (s.id,)).fetchone()[0]
                                  for s in snaps])
        self.db.execute("UPDATE snapshots SET kind='label', message=?, uploaded=? WHERE id=?",
                        (message, json.dumps(merged) if merged else "", keep.id))
        removed = self.delete_snapshots([s.id for s in snaps[:-1]])
        self.db.commit()
        self.gc()
        kept = self.get(keep.id)
        assert kept is not None
        return kept, removed

    def prune(self, auto_days: int = 14, keep_last_auto: int = 30,
              now: float | None = None, dry_run: bool = False) -> list[int]:
        """오래된 auto 스냅샷 정리. label·HEAD·최신 keep_last_auto 개는 남긴다.

        auto_days 가 0 이하면 아무것도 하지 않는다. 반환: 지울(지운) id 목록(오름차순).
        """
        if auto_days <= 0:
            return []
        now = time.time() if now is None else now
        cutoff = now - auto_days * 86400
        head = self.head()
        autos = [s for s in self.log() if s.kind == "auto"]        # id 내림차순
        keep_ids = {s.id for s in autos[:max(0, keep_last_auto)]}
        victims = sorted(s.id for s in autos
                         if s.ts < cutoff and s.id not in keep_ids and (head is None or s.id != head.id))
        if dry_run or not victims:
            return victims
        self.delete_snapshots(victims)
        self.gc()
        return victims

    def gc(self, dry_run: bool = False, exclude_snapshots: list[int] | None = None) -> "GCResult":
        """어떤 tree 행도 참조하지 않는 객체 파일 삭제. 반환 (개수, 바이트) + meta (하위 호환 튜플).

        exclude_snapshots 의 스냅샷은 '이미 지워졌다' 치고 계산한다(정리 예고용).
        .tmp 잔여물과 빈 하위 폴더도 함께 치운다(개수에는 세지 않음).
        """
        ex = [int(i) for i in (exclude_snapshots or [])]
        q = "SELECT DISTINCT sha FROM tree"
        if ex:
            q += " WHERE snapshot_id NOT IN (%s)" % ",".join("?" * len(ex))
        refs = {r[0] for r in self.db.execute(q, ex).fetchall()}
        # baseline(마지막으로 올린 상태)이 가리키는 객체·사이드카는 절대 지우지 않는다
        refs |= {r[0] for r in self.db.execute("SELECT sha FROM baseline").fetchall()}
        metas = self._gc_meta(refs, dry_run)
        if not self.objects.exists():
            return GCResult(0, 0, metas)
        count = 0
        freed = 0
        for p in list(self.objects.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix == ".tmp":
                if not dry_run:
                    p.unlink(missing_ok=True)
                continue
            if p.name in refs:
                continue
            freed += p.stat().st_size
            count += 1
            if not dry_run:
                p.unlink(missing_ok=True)
        if not dry_run:
            for d in sorted((d for d in self.objects.rglob("*") if d.is_dir()),
                            key=lambda d: len(d.parts), reverse=True):
                try:
                    d.rmdir()
                except OSError:
                    pass
        return GCResult(count, freed, metas)

    def _gc_meta(self, refs: set[str], dry_run: bool) -> int:
        """어떤 tree 행도 참조하지 않는 sha 의 의미 diff 사이드카 삭제. 반환 개수."""
        root = self.cfg.state_dir / "store" / "meta"
        if not root.exists():
            return 0
        n = 0
        for p in list(root.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix == ".tmp":
                if not dry_run:
                    p.unlink(missing_ok=True)
                continue
            if p.stem in refs:
                continue
            n += 1
            if not dry_run:
                p.unlink(missing_ok=True)
        return n

    def close(self) -> None:
        self.db.close()


class GCResult(tuple):
    """(객체 수, 바이트) 튜플 — 하위 호환. .meta 로 지운 사이드카 수."""

    def __new__(cls, count: int, freed: int, meta: int = 0):
        obj = super().__new__(cls, (count, freed))
        obj.meta = meta
        return obj


class SquashHasLabels(ValueError):
    """묶으면 사라질 이름 붙인 스냅샷이 있다. labels 에 [(id, message), ...]."""

    def __init__(self, msg: str, labels: list[tuple[int, str]] | None = None):
        super().__init__(msg)
        self.labels: list[tuple[int, str]] = list(labels or [])


class RestoreBlocked(RuntimeError):
    """적용 전 중단(파일 변경 없음): 브릿지 없음·dirty 확인 실패·저장 안 된 패키지·파일 잠김.

    dirty 에 패키지 목록, locked 에 잠긴(쓸 수 없는) 파일 rel 목록.
    """

    def __init__(self, msg: str, dirty: list[str] | None = None, locked: list[str] | None = None):
        super().__init__(msg)
        self.dirty: list[str] = list(dirty or [])
        self.locked: list[str] = list(locked or [])


def file_locked(path: Path) -> bool:
    """대상 파일이 다른 프로세스에 쓰기 잠겨 있나 — 'r+b' 로 열어만 본다(내용은 안 건드림)."""
    try:
        if not path.exists():
            return False
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def _replace_with_retry(tmp: Path, dst: Path, attempts: int = 5, delay: float = 0.2) -> None:
    """tmp → dst 이름 바꾸기. 에디터가 막 놓아주는 중이면 PermissionError 가 잠깐 날 수 있어 재시도."""
    for i in range(attempts):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def _partial_msg(rel: str, written: int, safety: "Snapshot | None") -> str:
    msg = (f"파일이 잠겨 있어 쓰지 못했다: {rel} — 에디터에서 그 애셋의 편집 창을 닫고 다시 시도하라")
    if written:
        sid = safety.id if safety is not None else "?"
        msg += f"\n(파일 {written}개는 이미 바뀌었다 — 안전 스냅샷 #{sid} 으로 되돌릴 수 있음)"
    return msg


def cleanup_tmp_files(cfg: Config, max_age_minutes: float = 10.0) -> int:
    """Content 아래에 남은 오래된 *.jokate-tmp 잔여물 삭제 → 지운 개수."""
    content = cfg.content
    if not content.exists():
        return 0
    cutoff = time.time() - max_age_minutes * 60
    n = 0
    for p in content.rglob("*.jokate-tmp"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


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
    safety_created: bool = True   # 안전 스냅샷을 새로 만들었나 (False 면 safety 는 롤백 직전 HEAD)


def _affected_rels(d: Diff) -> list[str]:
    """롤백이 실제로 건드리는 rel 집합 (modified·added(부활)·deleted + moved 의 old/new)."""
    rels = {n.rel for _, n in d.modified}
    rels |= {e.rel for e in d.added} | {e.rel for e in d.deleted}
    for o, n in d.moved:
        rels.add(o.rel)
        rels.add(n.rel)
    return sorted(rels)


def _select_diff(d: Diff, only: list[str] | None) -> Diff:
    """pending diff 에서 only 의 rel 만 고른다(이동은 old/new 중 하나만 골라도 쌍으로)."""
    if not only:
        return d
    want = {str(a).replace("\\", "/").strip("/") for a in only}
    return Diff(
        added=[e for e in d.added if e.rel in want],
        modified=[(o, n) for o, n in d.modified if n.rel in want],
        deleted=[e for e in d.deleted if e.rel in want],
        moved=[(o, n) for o, n in d.moved if o.rel in want or n.rel in want],
    )


def _uploaded_items(d: Diff) -> list[dict]:
    """이번에 올린 항목을 snapshots.uploaded 에 넣을 JSON 목록으로."""
    items: list[dict] = []
    for e in d.added:
        items.append({"rel": e.rel, "state": "added", "old_sha": None, "new_sha": e.sha,
                      "size": e.size, "old_size": 0, "cls": e.cls, "noise": False})
    for o, n in d.modified:
        items.append({"rel": n.rel, "state": "modified", "old_sha": o.sha, "new_sha": n.sha,
                      "size": n.size, "old_size": o.size, "cls": n.cls, "noise": bool(n.noise)})
    for o, n in d.moved:
        items.append({"rel": n.rel, "old_rel": o.rel, "state": "moved", "old_sha": o.sha, "new_sha": n.sha,
                      "size": n.size, "old_size": o.size, "cls": n.cls, "noise": False})
    for e in d.deleted:
        items.append({"rel": e.rel, "state": "deleted", "old_sha": e.sha, "new_sha": None,
                      "size": e.size, "old_size": e.size, "cls": e.cls, "noise": False})
    return items


def _diff_from_uploaded(items: list[dict]) -> Diff:
    d = Diff()
    for it in items:
        cls = it.get("cls") or ""
        new = TreeEntry(it["rel"], it.get("new_sha") or "", int(it.get("size") or 0), cls,
                        noise=bool(it.get("noise")))
        old = TreeEntry(it.get("old_rel") or it["rel"], it.get("old_sha") or "",
                        int(it.get("old_size") or 0), cls)
        state = it.get("state")
        if state == "added":
            d.added.append(new)
        elif state == "deleted":
            d.deleted.append(old)
        elif state == "moved":
            d.moved.append((old, new))
        else:
            d.modified.append((old, new))
    for lst in (d.added, d.deleted):
        lst.sort(key=lambda e: e.rel)
    for lst in (d.modified, d.moved):
        lst.sort(key=lambda t: t[1].rel)
    return d


def _merge_uploaded(raw: list[str]) -> list[dict]:
    """묶이는 스냅샷들의 uploaded 를 rel 기준으로 합친다(첫 old_sha + 마지막 new_sha)."""
    merged: dict[str, dict] = {}
    for blob in raw:
        for it in (json.loads(blob) if blob else []):
            cur = merged.get(it["rel"])
            if cur is None:
                merged[it["rel"]] = dict(it)
                continue
            first_old, first_old_size = cur.get("old_sha"), cur.get("old_size", 0)
            first_rel = cur.get("old_rel")
            cur.update(it)
            cur["old_sha"] = first_old
            cur["old_size"] = first_old_size
            if first_rel:
                cur["old_rel"] = first_rel
            if first_old is None and it.get("state") != "deleted":
                cur["state"] = "added"
            elif it.get("state") == "deleted":
                cur["state"] = "deleted"
    out = []
    for it in merged.values():
        if it.get("old_sha") and it.get("old_sha") == it.get("new_sha") and it.get("state") != "moved":
            continue        # 올렸다가 되돌아온 것은 결과적으로 변경 없음
        if it.get("old_sha") is None and it.get("new_sha") is None:
            continue
        out.append(it)
    return out


def _pkg_of(rel: str) -> str:
    return "/Game/" + rel.rsplit(".", 1)[0]


def _tasklist_has_editor() -> bool:
    """tasklist 에 UnrealEditor.exe 가 하나라도 있으면 True (프로젝트 구분 없음)."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq UnrealEditor.exe", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return False
    return "unrealeditor.exe" in out.lower()


def query_editor_cmdlines(timeout: float = 10.0) -> list[str] | None:
    """실행 중인 UnrealEditor.exe 들의 명령줄 목록. 조회 실패면 None(판정 불가)."""
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
           "Get-CimInstance Win32_Process -Filter \"Name='UnrealEditor.exe'\" "
           "| ForEach-Object { $_.CommandLine }"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]


def _norm_path(p) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def editor_running(cfg: Config | None = None, *, query=None) -> bool:
    """UnrealEditor.exe 가 떠 있는지. Windows 가 아니면 False.

    cfg 를 주면 '이 프로젝트의' 에디터만 센다 — 프로세스 명령줄에 이 프로젝트의 .uproject 나
    프로젝트 폴더 경로가 들어 있는 것만(대소문자·슬래시 방향 무시). 명령줄 조회가 실패하면
    보수적으로 기존 tasklist 방식으로 폴백한다.
    """
    if sys.platform != "win32":
        return False
    if cfg is None:
        return _tasklist_has_editor()
    lines = (query or query_editor_cmdlines)()
    if lines is None:                      # 조회 불가 → 보수적으로 (다른 프로젝트여도 참)
        return _tasklist_has_editor()
    root = _norm_path(cfg.root)
    names = {_norm_path(p) for p in Path(cfg.root).glob("*.uproject")}
    names.add(_norm_path(Path(cfg.root) / (Path(cfg.root).name + ".uproject")))
    for ln in lines:
        s = _norm_path(ln)
        if any(n and n in s for n in names):
            return True
        if root and root in s:
            return True
    return False


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


def deps_change(old: TreeEntry, new: TreeEntry) -> tuple[list[str], list[str]]:
    """두 버전의 하드 참조 목록을 비교 → (추가된 패키지, 사라진 패키지). 둘 다 정렬된 목록."""
    o, n = set(old.deps or []), set(new.deps or [])
    return sorted(n - o), sorted(o - n)


DEPS_MAX_LINES = 6


def _dep_lines(old: TreeEntry, new: TreeEntry) -> list[str]:
    """수정·이동 행 아래에 붙일 들여쓴 참조 변화 줄(최대 DEPS_MAX_LINES + 요약 1줄)."""
    added, removed = deps_change(old, new)
    items = [f"    + {p}" for p in added] + [f"    - {p}" for p in removed]
    if len(items) > DEPS_MAX_LINES:
        rest = len(items) - DEPS_MAX_LINES
        items = items[:DEPS_MAX_LINES] + [f"    … 외 {rest}개"]
    return items


def dep_pkg(rel: str) -> str:
    """Content 기준 상대경로 → /Game/... 패키지 경로."""
    r = rel.replace("\\", "/").strip("/")
    i = r.rfind(".")
    return "/Game/" + (r[:i] if i > r.rfind("/") else r)


def format_diff(d: Diff) -> str:
    lines = []
    for e in d.added:
        lines.append(f"  A {e.rel}  [{e.cls or '?'}]")
    for o, n in d.modified:
        if n.noise:
            lines.append(f"  M~ {n.rel}  [{n.cls or '?'}]  {o.size}→{n.size}B  (리세이브만)")
        else:
            lines.append(f"  M {n.rel}  [{n.cls or '?'}]  {o.size}→{n.size}B")
        lines += _dep_lines(o, n)
    for o, n in d.moved:
        lines.append(f"  R {o.rel} → {n.rel}  [{n.cls or '?'}]")
        lines += _dep_lines(o, n)
    for e in d.deleted:
        lines.append(f"  D {e.rel}  [{e.cls or '?'}]")
    if not lines:
        lines.append("  (변경 없음)")
    bc = d.by_class()
    if bc:
        lines.append("클래스별:")
        for cls in sorted(bc):
            c = bc[cls]
            parts = [f"{k} {c[k]}" for k in ("added", "modified", "resave", "moved", "deleted") if c[k]]
            lines.append(f"  {cls:<24} " + ", ".join(parts))
    return "\n".join(lines)


def format_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
