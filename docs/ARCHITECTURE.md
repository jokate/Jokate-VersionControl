# 내부 구조

사용법은 [../README.md](../README.md), HTTP API 는 [API.md](API.md).

## 모듈 구성

- `jokate/uasset.py` — 패키지 헤더 파서 (요약·이름·임포트·익스포트·썸네일). UE 4.11 ~ 5.7 검증
- `jokate/scan.py` — 등급 분류 + 애셋 레코드(클래스·부모·의존성·해시)
- `jokate/store.py` — 스냅샷 저장소 (내용주소 객체 + SQLite 인덱스, 트리 diff, 롤백)
- `jokate/meta.py` — 의미 diff 사이드카 (DataTable 값 표 + 일반 애셋 T3D 텍스트 비교)
- `jokate/watch.py` — 저장 감지 자동 스냅샷 (폴링 + debounce, `poll_once` 순수 함수)
- `jokate/daemon.py` — 창 없는 단일 데몬 (웹 + watch 스레드, 상태 파일·로그, pause/resume/stop)
- `jokate/web.py` + `jokate/web_static/index.html` — 타임라인 웹 UI (API 로직은 `api_*` 순수 함수)
- `jokate/bridge.py` — 에디터 브릿지 도구 쪽 (`bridge_alive`, `request`, `install`)
- `jokate/ue/jokate_bridge.py` — 에디터 쪽 (UE Python, `import unreal`, 콘텐츠 브라우저 메뉴)
- `jokate/ue/jokate_client.py` — 웹 API 클라이언트 (urllib 만, `unreal` 미사용 → 가짜 서버로 테스트)
- `jokate/ue/jokate_launch.py` — 데몬 실행기 (tool.json, `PYTHON*` 청소, 창 없는 Popen)
- `jokate/uediff.py` — UE diff 창 실행 + 에디터 경로 탐색
- `jokate/tray.py` — 윈도우 트레이 아이콘 (ctypes, `menu_items` 순수 함수)
- `jokate/config.py` — 프로젝트 설정 / `jokate/__main__.py` — CLI

## 스냅샷 저장소

- `.jokate/store/objects/<sha[:2]>/<sha>` — 원본 그대로(압축 없음), 내용주소(blake2b-256). 같은 내용은 한 번만 저장
- `.jokate/index.sqlite` — `snapshots(id, parent, kind, message, ts, uploaded)`, `tree(snapshot_id, rel, sha, size, cls, deps, noise)`, `baseline(rel, sha, size, cls, deps)`, `store_meta(k, v)`
- 변경 판단은 mtime 이 아니라 sha 비교. 이동은 "sha 동일 + 경로 변경" 으로 잡는다
- vendor 등급은 스냅샷에 포함하지 않는다
- 옛 DB 는 `Store` 를 열 때 `ALTER TABLE tree ADD COLUMN noise` 로 자동 마이그레이션(기존 행 0)

## 확정 버전과 작업 중 기록

스냅샷은 `snapshots.role` 로 두 부류다 (`kind` 는 하위 호환으로 남는다).

- `role='fix'` — **확정 버전**. 사용자가 `confirm` 한 것만. 영구 이력이라 prune 이 절대 지우지 않는다
- `role='journal'` — **작업 중 기록**. 자동 저장, 되돌리기 안전 스냅샷, 되돌리기 결과. 확정하거나 변경을 버리는 순간 지워진다
- `confirm(message, only=[rel])` — 기존 `upload` 와 같은 일(전체 작업 트리 스냅샷 + `uploaded` 기록 + baseline 갱신, `role='fix'`) 뒤에 **이 확정 이전의 journal 을 전부 지우고 gc** 한다. 일부만 확정해도 journal 은 전부 지운다 — 확정하지 않은 애셋의 현재 상태는 디스크와 새 fix 스냅샷의 tree 에 남아 계속 '확정 안 된 변경'으로 보인다. 확정할 게 없으면 `snapshot=None`(아무것도 안 지움). 반환 튜플에 `.cleared`(지운 journal 수). `upload` 는 별칭
- `revert_to_baseline`(변경 버리기) — 적용에 성공하면 이번 되돌리기의 안전 스냅샷 하나만 '실행 취소 지점'(`RestoreResult.undo`)으로 남기고 나머지 journal(되돌리기 결과 포함)을 지운다. 안전 스냅샷이 새로 안 만들어졌으면(대상이 HEAD 와 같았음) 전부 지우고 `undo=None`. 남은 지점은 다음 확정·다음 버리기 때 사라진다
- `plan_restore`/`apply_restore`(과거 버전으로) — 안전·결과 스냅샷 모두 `role='journal'`, `kind='auto'`. baseline 은 그대로라 되돌린 결과는 '확정 안 된 변경'으로 보이고, 확정하면 새 fix 가 된다
- 조회: `log(role=None)` · `fix_log()` · `journal()` · `head_fix()`. `squash` 는 fix 끼리만 보호 확인(사슬에 낀 journal 은 함께 사라진다)
- 마이그레이션(`ALTER TABLE snapshots ADD COLUMN role`): `uploaded` 가 있는 label → fix, `롤백…` label → journal, 그 밖의 label → fix, auto → journal. fix 가 하나도 없으면 가장 오래된 label 을 fix 로

## baseline 과 자동 기록

'올린 것'과 '자동 기록'은 다른 축이다 (git 의 index/commit 대 autosave).

- `baseline(rel, sha, size, cls, deps)` — 사용자가 **마지막으로 올린(인정한) 상태**. `snapshots.uploaded` 는 그 스냅샷에서 **이번에 올린 항목**만 담는 JSON(`{rel, state, old_sha, new_sha, old_rel?, size, cls, noise}`)
- `status()` = baseline → 작업 트리 Diff. 자동 스냅샷(`kind=auto`)과 롤백은 baseline 을 건드리지 않으므로, 데몬이 5초 뒤 자동 기록을 남겨도 '올리지 않은 변경'은 그대로 남는다
- `confirm(message, only=[rel])`(옛 이름 `upload`) — 고른 변경만 baseline 에 반영(삭제는 baseline 에서 제거, 이동은 old/new 중 하나만 골라도 쌍으로). 스냅샷은 항상 **전체 작업 트리**로 찍고(HEAD 와 트리가 같아도 생성) `uploaded` 에 올린 항목을 기록한다. 올릴 게 없으면 `None`
- `show`/`log` 는 `uploaded` 가 있으면 그것을 Diff 로 보여주고(직전 대비가 아니라 '이번에 올린 것'), 없으면 직전 스냅샷 대비
- `squash` 는 묶이는 스냅샷들의 `uploaded` 를 rel 기준으로 합친다(첫 `old_sha` + 마지막 `new_sha`, 결과가 같아지면 제외)
- `gc`/`prune` 은 baseline 이 참조하는 sha 의 객체·meta 사이드카를 절대 지우지 않는다
- `revert_to_baseline([rel])` — 고른 애셋을 마지막으로 올린 상태로 되돌리기. `plan_restore` 와 같은 경로(안전 스냅샷·dirty 차단·reload·참조 검산)를 쓰고 '대상 트리'만 baseline 으로 바꾼다
- 마이그레이션: baseline 이 비어 있고 `store_meta.baseline_ready` 가 없으면 가장 최근 label 스냅샷(없으면 HEAD)의 트리로 한 번 채운다. `snapshots.uploaded` 는 `ALTER TABLE` 로 추가

## 부분 스냅샷과 롤백

- `snap(..., kind=...)` 의 `only=[rel]` — 지정한 애셋만 새 상태로 올리고 나머지는 HEAD 유지(내부용: 롤백의 안전·결과 스냅샷). `kind` 없이 메시지를 주면 `upload` 로 넘어간다
- `plan_restore(sid, assets)` 는 드라이런: 되돌릴 애셋(M 수정 되돌림 / A 부활 / R 이동 / D 삭제) + 클래스별 집계 + 참조 검산만 만든다
- 참조 검산: 결과 트리 각 애셋의 `/Game/` 의존성이 결과 트리·vendor·현재 디스크 어디에도 없으면 "깨질 참조"(`broken`), 롤백으로 사라지는 애셋을 참조하는 authored 애셋은 `dependents` 경고
- 에디터 파일 잠금: 에디터는 로드한 패키지의 `.uasset` 을 공유 없이 열어 둬 밖에서 덮어쓰면 `PermissionError [WinError 5]` 가 난다 → 쓰기 전에 브릿지 `release` 로 `unload_packages` 하고, 그래도 잠긴 파일이 있으면 `file_locked()` 사전 검사에서 아무것도 쓰지 않고 `RestoreBlocked(locked=[...])`. `tmp→replace` 는 `PermissionError` 시 0.2초 간격 5회 재시도, 성공·실패 무관하게 `finally` 에서 이번 실행의 `*.jokate-tmp` 삭제. 오래된(10분+) 잔여물은 데몬 시작·CLI restore 시작 때 `cleanup_tmp_files(cfg)` 로 정리
- `apply_restore` 순서: ⓪ dirty 확인 → `release` → 사전 잠금 검사 ① auto 부분 스냅샷 `롤백 직전 #<id>`(안전망) ② 객체를 `Content/` 로 복사(tmp→replace) ③ 결과 트리에 없는 authored 파일 삭제 ④ label 부분 스냅샷 `롤백: #<id>`
- 두 스냅샷은 이번 롤백이 건드리는 rel 만 담는 부분 스냅샷이다 → 무관한 애셋의 올리지 않은 변경은 롤백 뒤에도 '현재 변경사항' 에 남는다
- 되돌릴 애셋의 디스크 상태가 HEAD 와 같으면 안전 스냅샷을 새로 만들지 않고 HEAD 를 직전 상태로 쓴다(`RestoreResult.safety_created=False`)
- 객체 존재 검사는 실제로 쓸 항목에만 한다. 유실됐으면 아무것도 바꾸기 전에 `FileNotFoundError`
- `UnrealEditor.exe` 가 실행 중이면 브릿지가 필요하다. 없으면 `--apply` 거부
- 롤백도 되돌릴 수 있다: `restore <project> <안전 스냅샷 id> --apply`

## 정리 (squash · prune · gc)

- 스냅샷은 전부 '전체 트리' 라서 중간 스냅샷을 지워도 남은 스냅샷은 온전하다. 지운 스냅샷을 부모로 가진 스냅샷은 살아남은 조상으로 다시 이어지고 `noise` 는 새 부모 기준으로 재계산된다. HEAD 는 안 지운다
- `squash(ids, message, include_labels=False)`: 부모-자식으로 연속된 사슬만 허용(아니면 `ValueError`). 마지막 하나만 `kind=label` + 메시지로 남긴다. 사라질 쪽에 라벨이 있으면 `SquashHasLabels`(`.labels=[(id, message)]`)
- `prune(auto_days, keep_last_auto, now, dry_run)`: `kind=auto` 이고 오래됐고 최신 auto `keep_last_auto` 개에 안 들고 HEAD 가 아닌 것만 삭제
- `gc(dry_run)`: 어떤 tree 행도 참조하지 않는 객체 파일·의미 diff 사이드카 삭제 + 빈 폴더·`.tmp` 정리 → `(개수, 바이트)`. squash·prune 뒤 자동 실행, 모든 삭제는 한 트랜잭션
- 데몬은 시작 시 한 번, 이후 24시간마다 prune+gc 를 돌리고 지운 게 있을 때만 `daemon.log` 에 한 줄 남긴다

## noise(리세이브) 판정

- 에디터가 내용 변경 없이 다시 저장하면 SavedHash·엔진 버전·썸네일·오프셋 등 헤더만 바뀌어 sha 가 달라진다
- `snap` 시 HEAD 대비 sha 가 바뀐 애셋마다 `uasset.is_resave_only(HEAD 객체, 현재 파일)` 로 판정: 이름·임포트·익스포트 표가 같고 각 export 직렬화 바이트가 동일하면 `tree.noise=1`(파싱 실패·예외는 0)
- `Diff.resave` / `real_modified` / `all_noise`, `by_class()` 는 noise 항목을 `modified` 대신 `resave` 로 집계. 변경 여부(`empty`) 판정은 그대로(리세이브도 스냅샷은 만든다)
- 표시: `status`/`show` 는 `M~ rel [cls] (리세이브만)`, `log` 는 전체가 리세이브면 줄 끝에 `(리세이브만)`, `watch` 는 `리세이브만 N`

## 의미 diff 사이드카

- `.jokate/store/meta/<sha[:2]>/<sha>.json` = `{kind, row_struct, columns, rows:{행이름:{열:"값"}}}` — 값은 모두 문자열
- 스냅샷을 찍는 순간(데몬 자동·웹 올리기·CLI `snap`) 브릿지가 살아 있으면 에디터에 로드된 현재 버전을 `export_meta` 로 받아 그 파일의 sha 를 키로 저장한다. 과거 `.uasset` 을 다시 로드하지 않는다
- 한계: 에디터가 꺼져 있거나 브릿지가 없으면 그 버전은 기록이 없다. dirty 패키지는 건너뛴다. 응답 후 파일 sha 가 달라졌으면 버린다. 두 버전 모두 사이드카가 있어야 비교된다
- 일반 애셋(Text): 클래스가 `DataAsset` 으로 끝나거나 `[meta] text_classes`(fnmatch) 에 걸리면 `AssetExportTask` + `ObjectExporterT3D` 로 `Saved/JokateMeta/` 에 잠깐 내보내 읽고 파일을 지운다. 사이드카는 `{kind:"Text", cls, text}`, 512KB 초과는 건너뛰고 errors 에 사유. Blueprint·머티리얼·텍스처·메시·애니메이션·레벨은 기본 제외
- 비교는 `normalize_t3d`(줄 끝 공백·ExportPath·GUID·포인터 마스킹) → `summarize_props`(속성 단위 요약) + `diff_text`(문맥 3줄 줄 diff). gc 의 고아 사이드카 정리는 kind 와 무관

## 데몬 · watch

- watch: 외부 의존성 없이 폴링 — authored 폴더의 `(size, mtime)` 맵을 `--interval` 초마다 비교, vendor/ignore 최상위 폴더는 훑지 않는다. 변화 뒤 `--debounce` 초 조용하면 auto 스냅샷(연속 저장을 하나로 묶음). 실제 변경 판단은 sha 비교. 시작 시 즉시 한 번. 로그는 stdout + `.jokate/watch.log`
- daemon: 한 프로세스에서 웹 서버 스레드 + `poll_once` 감시 루프 스레드. 콘솔 창 없음(`pythonw`), 로그는 `.jokate/daemon.log`
- 상태 파일 `.jokate/daemon.json` `{pid, port, started, tool_dir}` — 시작 시 쓰고 종료 시 지운다. pid 가 살아 있고 그 포트의 `/api/info` 가 응답하면 '이미 실행 중'
- 트레이: `ctypes` 만으로 숨은 메시지 창 + `Shell_NotifyIconW`. 메뉴 `타임라인 열기` / `지금 스냅샷` / `일시정지·재개` / `종료`. `지금 스냅샷` 은 플래그만 세우고 실제 스냅은 watch 스레드가 찍는다(SQLite 스레드 고정). 실패해도 데몬은 계속 돈다. 끄려면 `[daemon] tray = false`

## 에디터 브릿지 파일 프로토콜

에디터가 켜진 상태에서도 롤백을 적용하기 위한 파일 기반 프로토콜. 외부 의존성 없음, `<project>/.jokate/bridge/`:

| 파일 | 쓰는 쪽 | 내용 |
|---|---|---|
| `heartbeat.json` | 에디터 | 1초마다 `{"ts"}` 갱신. 3초 넘게 오래되면 브릿지 없음으로 본다 |
| `request.json` | 도구 | `{"id","op","packages":["/Game/..."],"args"}` |
| `response-<id>.json` | 에디터 | `{"id","ok",...}` |

- `bridge-install <project>`: `jokate/ue/` 의 스크립트를 `<project>/Content/Python/` 으로 복사하고 `init_unreal.py` 에 `import jokate_bridge` 한 줄을 보장. `.py` 는 애셋이 아니라 스캐너가 무시한다
- 에디터 쪽: `unreal.register_slate_post_tick_callback` 으로 1초마다 폴링. `dirty` → 요청 패키지 중 저장 안 된 것 목록. `release` → `find_package` 로 로드된 패키지를 찾아 `AssetEditorSubsystem.close_all_editors_for_asset`(실패 무시) 후 `unload_packages` → `{released, not_loaded, failed}` (파일 잠금 해제용). `reload` → dirty 대상이 있으면 `ok=false`(`args.discard_dirty` 면 통과), 존재하는 패키지는 `load_package` 후 `reload_packages(ASSUME_POSITIVE)`, 대상 폴더를 `scan_paths_synchronous(force_rescan)`. 예외는 `ok=false, error`
- `restore --apply` 는 에디터 실행 중이면: 브릿지 없음 → 거부 / `dirty` 요청 → 저장 안 된 대상이 있으면 중단(`--discard-dirty` 로 통과) / 파일 쓰기·삭제 후 `reload` 요청
- 선택 애셋 → 패키지명 → `package_to_rel`(`/Game/A/B` → `A/B.uasset`, 디스크에 `.umap` 이 있으면 `.umap`). `/Game` 밖은 무시

### 데드락 회피

HTTP 는 항상 `threading.Thread` 에서 보내고 결과는 큐에 넣어 기존 틱(`_tick`)에서 꺼내 `unreal.log` + 모달(`unreal.EditorDialog.show_message`)을 띄운다. 게임 스레드에서 동기로 부르면 서버가 같은 에디터의 브릿지(dirty/reload)를 기다리므로 데드락이 난다. 모달은 틱에서만 띄우고, 창이 떠 있는 동안 큐 처리는 재진입하지 않는다.

## 에디터를 켜면 자동 실행

- `bridge-install` 이 `.jokate/tool.json` `{tool_dir, python, pythonw, autostart}` 를 쓰고 `jokate_launch.py` 도 복사한다. 도구를 옮기거나 파이썬을 바꿨으면 다시 실행
- 에디터가 뜨면 브릿지가 워커 스레드에서 `GET /api/daemon` 을 2초 타임아웃으로 확인 → 응답이 있으면 아무것도 안 하고(다만 `serve` 단독이면 경고), 연결 실패면 `pythonw -m jokate daemon <project>` 를 DETACHED 로 띄운다
- 자식 프로세스 환경에서 `PYTHON*` 변수(UE 의 `PYTHONHOME`/`PYTHONPATH` 등)를 모두 지운다. 안 지우면 시스템 파이썬이 UE 파이썬 경로로 오염되어 바로 죽는다
- 끄는 법: `JOKATE_NO_AUTOSTART=1`, 또는 `[editor] autostart = false` 후 `bridge-install` 재실행

## uediff 의 에디터 탐색

- **에디터가 켜져 있으면 그 에디터 안에서 연다**(새 에디터를 띄우면 1분쯤 걸리므로). `store.editor_running()` + `bridge.bridge_alive()` 가 참이면 버전 파일을 `<project>/Saved/JokateDiff/<sha8>/<원래이름>`(전략 1: 파일 경로로 `load_package`)과 `<project>/Content/_JokateDiff/<sha8>/<원래이름>`(전략 2: `/Game/_JokateDiff/<sha8>/<이름>` 로 `load_asset`) 두 곳에 꺼내고 브릿지 op `diff`(타임아웃 60초)를 보낸다. 에디터 쪽은 `AssetToolsHelpers.get_asset_tools().diff_assets(old, new, RevisionInfo, RevisionInfo)` 를 게임 스레드에서 호출하고 성공한 전략(`file`/`package`)을 돌려준다 → `{mode:"editor", strategy}`
- 파일 이름은 반드시 원래 이름을 유지한다(바꾸면 패키지 안 애셋 이름과 어긋나 로드 실패). `_JokateDiff` 최상위 폴더는 `Config.tier_of` 가 설정과 무관하게 `None`(무시) 로 판정해 절대 추적되지 않는다. 두 임시 폴더는 24시간 뒤(데몬 시작 시 `cleanup_tmp`) 정리한다
- **에디터가 켜져 있으면 절대 두 번째 에디터를 띄우지 않는다.** 브릿지가 꺼져 있으면 `DiffBlocked`('에디터는 켜져 있는데 브릿지가 꺼져 있습니다 — Jokate > 브릿지 켜기'), 브릿지 op 가 실패하면 브릿지가 돌려준 `error` + 시도한 전략 이름을 담아 `DiffBlocked`. 웹은 409 `{ok:false, error, mode:"editor"}` 로 모달 안내, CLI 는 exit code 2
- `plan_diff(store)`(= `GET /api/uediff/plan`)가 `mode`(editor/process)를 미리 알려준다. UI 는 `process` 면 '에디터가 꺼져 있습니다 … 띄울까요?' 확인 모달을 먼저 띄운다
- 에디터가 꺼져 있을 때만 새 프로세스 경로: 두 버전을 `<project>/.jokate/tmp/diff/<이름>__<sha8>.<확장자>` 로 꺼낸 뒤 `UnrealEditor.exe <프로젝트.uproject> -diff <왼쪽> <오른쪽>` 을 창 분리로 실행한다. 임시 파일은 24시간 뒤 정리
- 에디터 경로: ① `[editor] exe` ② `.uproject` 의 `EngineAssociation` — 버전(`5.7`)이면 `HKLM\SOFTWARE\EpicGames\Unreal Engine\<버전>` 의 `InstalledDirectory`, GUID(소스 빌드)면 `HKCU\Software\Epic Games\Unreal Engine\Builds`
- 못 찾으면 웹은 409 로 안내한다

## 낡은 서버 감지와 재시작

- `web.BUILD_ID` = 서버가 뜰 때 계산한 `jokate/*.py` + `web_static/*` 의 (상대경로, mtime, size) 해시(10자). `GET /api/info` 가 `build`(시작 시) · `build_disk`(요청 시 다시 계산, 5초 캐시) · `stale` 을 준다
- UI 는 `stale` 이거나 어떤 API 가 404 이면서 `error` 가 요청 경로와 같으면(라우트 자체가 없음 = 낡은 서버) 상단 배너를 띄운다. '지금 재시작' → `POST /api/daemon {action:"restart"}` → 1초 간격으로 `/api/info` 를 폴링해 `build` 가 바뀌면 새로고침
- `DaemonControl.restart()` 는 `spawn_daemon()` 으로 같은 인자의 데몬을 DETACHED 로 띄우고(`PYTHON*` 환경변수 제거, `JOKATE_WAIT_PORT=1`) 자신은 `stop()`. 새 프로세스는 `JOKATE_WAIT_PORT=1` 을 보면 이전 데몬이 물러날 때까지 최대 15초 기다린 뒤 뜬다. `serve` 단독 모드에는 컨트롤이 없어 409 → UI 가 'start.bat 을 다시 실행하세요' 로 안내

## 에디터 툴 메뉴

- 상단 **툴(Tools) > Jokate**: 브릿지 켜기/끄기, 데몬 시작·재시작, 타임라인 열기(웹), 지금 스냅샷, 상태 보기. 메뉴 등록은 `init_unreal.py` 의 `import jokate_bridge` 에서 항상 수행하고, 브릿지 틱은 `tool.json` 의 `autostart` 에 따른다(false 면 메뉴에서 켠다)
- 콘텐츠 브라우저 우클릭 **Jokate** 에는 '직전 스냅샷과 비교(diff)' 가 추가됐다(HEAD 버전 ↔ 현재 파일). 모든 HTTP 는 워커 스레드, 모달은 틱에서만

## 리비전 컨트롤 플러그인 (ue-plugin/JokateSourceControl)

에디터 모듈 하나(`Type: Editor`, `LoadingPhase: Default`)로 구성한다.

- `FJokateSourceControlModule` — `StartupModule` 에서 `IModularFeatures` 에 `"SourceControl"` 피처로 프로바이더를 등록하고 `ShutdownModule` 에서 `Close()` 후 해제한다. 설정 접근자(`AccessSettings`)를 갖는다
- `FJokateSourceControlSettings` — 포트(기본 8765). `SourceControlHelpers::GetSettingsIni()` 의 `[JokateSourceControl.JokateSourceControlSettings]` 에 저장하고, `<ProjectDir>/.jokate/daemon.json` 의 `port` 가 있으면 그쪽을 우선한다
- `FJokateSourceControlProvider` — `Init` 이 `GET /api/ping` 으로 `root` 가 이 프로젝트인지 확인해 `bAvailable` 을 정한다. `Execute` 는 이번 단계에서 `Connect`·`UpdateStatus` 만 처리하고 나머지는 '지원 안 함' 메시지와 함께 `Failed`. 체크아웃·체인지리스트는 쓰지 않는다(`UsesCheckout/UsesChangelists = false`, `UsesFileRevisions = true`)
- `FJokateSourceControlState` — 절대경로 + `EJokateFileState`(Unknown/Clean/Modified/Added/Deleted/Untracked/Missing). `CanEdit` 은 항상 true(잠금 없음), `CanCheckIn/CanRevert` 는 Modified·Added·Deleted. 히스토리는 22c 에서 채운다
- `JokateSourceControlUtils` — 절대경로 ↔ Content 기준 rel(슬래시 통일, Content 밖은 Untracked) 변환과 `/api/states` 응답 파싱

### HTTP 스레드 정책

`FJokateHttp` 는 `FHttpModule` 요청에 `SetDelegateThreadPolicy(EHttpRequestDelegateThreadPolicy::CompleteOnHttpThread)` 를 걸고 `FEvent` 로 기다린다. 완료 콜백이 게임 스레드 틱을 기다리지 않으므로 게임 스레드에서 동기 호출해도 데드락이 없다(기본 10초, 확정·버리기는 60초). `EConcurrency::Asynchronous` 로 들어온 `Execute` 는 스레드풀로 넘긴 뒤 `AsyncTask(ENamedThreads::GameThread)` 로 돌아와 상태 캐시 갱신과 완료 델리게이트를 호출한다. 상태 캐시(`TMap<절대경로, State>`)는 게임 스레드에서만 만지고, 변경은 `OnSourceControlStateChanged` 브로드캐스트(그리고 `Tick` 의 보류 플래그)로 알린다.

`UpdateStatus` 흐름: 요청 파일 → Content 기준 rel → `POST /api/states {rels}` → 캐시 갱신 → 브로드캐스트. 파일 목록이 비면 `rels` 를 빈 배열로 보내 전체를 받는다.

## UE 패키지 헤더 포맷 메모

`jokate/uasset.py` 를 읽으며 확인한 것들:

- legacy 버전 `-9`(UE 5.6+): 버전 바로 뒤에 `SavedHash`(FIoHash, 20바이트)가 오고 `Guid`/`Generations` 가 사라진다. 필드 순서도 `-9` 는 `SavedHash, TotalHeaderSize, CustomVersions`, 그 이전은 `CustomVersions, TotalHeaderSize`
- 파일 버전 `1018`(5.7)부터 헤더에 8바이트가 추가된다(관측값 0, 용도 미확인)
- `1017` 과의 차이는 PersistentGuid/OwnerPersistentGuid 유무 등으로 분기한다
- 썸네일: JPEG 는 높이가 음수로 저장되어 있어 `abs()` 로 읽는다. DataTable 처럼 썸네일이 없는 항목은 건너뛴다
- UE 는 bool 을 uint32 로, 음수 길이 문자열을 UTF-16LE(널 포함)로 직렬화한다
