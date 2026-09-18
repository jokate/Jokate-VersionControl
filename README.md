# Jokate-VersionControl

Unreal Engine 애셋 전용 로컬 버전관리. 원격·결제 없음. `Content/` 안의 `.uasset` / `.umap`만 다룬다.

## 왜

- 에디터 안에서 애셋이 계속 바뀌는데 "뭐가 바뀌었는지" 파일 diff로는 알 수 없다
- 롤백이 무서우면 아무도 안 쓴다 → 롤백은 항상 되돌릴 수 있어야 한다

## 두 등급

| 등급 | 대상 | 동작 |
|---|---|---|
| `vendor` | Paragon, 마켓 팩 등 가져온 것 | 동결. 해시 검증만. 히스토리에 안 섞임 |
| `authored` | 직접 만든 것 | 저장 즉시 자동 스냅샷. 히스토리·롤백 대상 |

폴더 규칙은 `<project>/.jokate/config.toml`.

## 사용

```
python -m jokate init    <project>            # .jokate/config.toml 생성
python -m jokate scan    <project>            # Content 스캔 → .jokate/scan.json
python -m jokate table   <project> --tier authored [--cls Blueprint]
python -m jokate inspect <file.uasset> [--thumb out.jpg]
python -m jokate snap    <project> [-m "메시지"] [--only rel ...]  # authored 스냅샷. -m 있으면 label, 없으면 auto. 변경 없으면 생략(--force). --only 는 부분 스냅샷(지정한 것만 올리고 나머지는 HEAD 유지)
python -m jokate status  <project>                # HEAD 대비 아직 올리지 않은 변경 (없으면 '올릴 변경 없음')
python -m jokate log     <project>               # 스냅샷 목록
python -m jokate show    <project> <id>          # 직전 스냅샷 대비 추가(A)/수정(M)/이동(R)/삭제(D) + 클래스별 집계
python -m jokate restore <project> <id> [--asset rel ...] [--apply] [--discard-dirty]   # 롤백. 기본 드라이런, --apply 로 적용
python -m jokate squash  <project> <from_id> <to_id> [-m 메시지] [--include-labels]   # from..to 사슬을 마지막 하나로 묶고 라벨
python -m jokate prune   <project> [--days N] [--keep N] [--dry-run] # 오래된 auto 스냅샷 정리 (라벨은 안 지움)
python -m jokate gc      <project> [--dry-run]                       # 어떤 스냅샷도 안 쓰는 객체 파일 삭제
python -m jokate bridge-install <project>        # 에디터 브릿지 스크립트를 Content/Python 에 설치
python -m jokate bridge-status  <project>        # 브릿지 heartbeat 나이
python -m jokate watch   <project> [--interval 2] [--debounce 5]      # 저장 감지 자동 스냅샷 데몬 (Ctrl+C 종료)
python -m jokate serve   <project> [--port 8765]                      # 타임라인 웹 UI (http://127.0.0.1:8765/)
python -m jokate daemon  <project> [--port 8765]                      # 한 프로세스로 웹 UI + 자동 스냅샷 (start.bat 이 pythonw 로 창 없이 띄움)
python -m jokate daemon-stop <project>                                # 돌고 있는 데몬 종료 (.jokate/daemon.json 의 포트로 요청)
```

## 배치 파일 (더블클릭)

경로를 타이핑하지 않는다: 처음 한 번 `.uproject` 파일 선택 창이 뜨고 `project.local.txt`(gitignore)에 기억한다. `.uproject` 를 bat 위에 끌어다 놓아도 된다.

| 파일 | 하는 일 |
|---|---|
| `start.bat` | 창 없는 데몬 하나(`daemon` = 웹 UI + 자동 스냅샷)를 `pythonw` 로 띄우고 브라우저 열기. 이미 떠 있으면 브라우저만. bat 창은 바로 닫힘. `JOKATE_NO_BROWSER=1` |
| `stop.bat` | 돌고 있는 데몬 종료 (`daemon-stop`). 웹 화면의 종료 버튼과 같은 일 |
| `snap.bat` | 올리지 않은 변경을 보여주고 메시지를 받아 라벨 스냅샷 (비우면 취소) |
| `bridge-install.bat` | 에디터 브릿지 + 콘텐츠 브라우저 Jokate 메뉴 설치 |
| `change-project.bat` | 기억한 프로젝트를 지우고 다시 선택 |
| `_project.bat`, `tools/pick_project.ps1` | 공용 도우미 (직접 실행하지 않음). 처음 쓰는 프로젝트면 `init` 까지 실행 |

## 타임라인 웹 UI (serve)

- 표준 라이브러리 `http.server` 만 사용, 프레임워크 없음. 단일 HTML + vanilla JS, 다크 테마, 한국어
- 왼쪽 타임라인(label 진하게 / auto 흐리게, `수정 3 · 추가 1` 건수 + 클래스 배지), 오른쪽 선택 스냅샷의 A/M/R/D 목록
- 목록 항목을 클릭하면 하단에 그 애셋의 버전 히스토리 + 버전별 썸네일(패키지 헤더의 첫 썸네일)
- 상단 메시지 입력 + '스냅샷 만들기'(label), 왼쪽 상단 '현재 변경사항' 패널(HEAD 대비 아직 올리지 않은 A/M/R/D, 30초 자동 갱신), 우클릭 메뉴로 부분 올리기·되돌리기 (아래 'UI 조작')
- JSON API: `GET /api/log`, `GET /api/status`, `GET /api/snap/<id>`, `GET /api/asset?rel=`, `GET /api/thumb?sha=|?rel=`, `GET /api/search?q=`, `GET /api/restore/<id>[?asset=]`, `POST /api/snap {message, only?:[rel]}`, `POST /api/restore/<id> {assets?:[rel], discard_dirty?:bool}`
- `POST /api/restore/<id>` 는 plan_restore→apply_restore 실행. 성공 `{ok:true, safety, result, written, deleted, safety_created}`; 에디터 dirty·브릿지 없음으로 중단되면 409 `{ok:false, error, dirty:[...]}` (`store.RestoreBlocked`), 객체 유실(`FileNotFoundError`)도 409 `{ok:false, error}`, 그 외 500.

## UI 조작

- 선택: 애셋 목록(현재 변경사항 패널, 스냅샷 상세)에서 클릭 토글 · Shift 범위 · Ctrl 추가 · 헤더 체크박스 전체 선택/해제 · Ctrl+A 전체 · Esc 해제. 선택 수는 헤더 옆에 표시
- 현재 변경사항 패널 우클릭: '선택한 것만 올리기'(메시지 모달 → `POST /api/snap {only}`), '전부 올리기', '선택한 것 되돌리기 — 마지막 스냅샷 상태로'
- 스냅샷 상세 우클릭: '선택한 애셋을 이 시점으로 되돌리기', '이 스냅샷 전체로 되돌리기'. 애셋 히스토리 카드 우클릭: '이 버전으로 되돌리기'
- 되돌리기는 항상 드라이런 확인 모달을 먼저 띄운다: 변경 목록(M/A/R/D) + 클래스별 집계 + 참조 경고(빨강) + '되돌릴 애셋의 현재 상태만 안전 스냅샷으로 남습니다. 다른 애셋의 올리지 않은 변경은 그대로 유지됩니다'. 확인하면 `POST /api/restore/<id>` 적용 → '롤백 완료 #N · 안전 스냅샷 #M'(safety_created=false 면 '되돌리기 전 상태 #M') 토스트, 타임라인·변경사항 갱신
- 에디터에 저장 안 한 변경(dirty)으로 409 가 오면 모달에 dirty 목록과 '저장 안 한 변경 버리고 진행' 버튼(`discard_dirty:true` 재요청). 그 외 오류는 모달에 메시지
- 검색: 타임라인 헤더 입력창(`/` 키로 포커스, 250ms 디바운스 → `GET /api/search?q=`)에 애셋·클래스·메시지를 넣으면 일치하는 스냅샷만 남고 헤더에 'n개 일치'. 각 항목에 일치한 애셋 이름 최대 3개. ✕ 버튼·Esc 로 해제. 검색 중 스냅샷을 열면 상세 목록에서 일치한 행 왼쪽에 accent 막대
- 행 썸네일: 스냅샷 상세·롤백 확인 모달은 `/api/thumb?sha=`, 현재 변경사항 패널은 `/api/thumb?rel=`(삭제 행은 HEAD sha). 28px 둥근 사각, `loading=lazy`, 없으면 클래스 해시 색 + 첫 글자 플레이스홀더
- 롤백 성공 토스트에는 '실행 취소' 버튼이 10초간 남는다: 확인 모달 없이 `POST /api/restore/<안전 스냅샷>` 으로 되돌리고 '실행 취소 완료 #N' 토스트. 409 등 오류면 기존 되돌리기 모달 흐름으로 넘어간다
- 컨텍스트 메뉴는 화면 밖으로 나가지 않으며 Esc·바깥 클릭·스크롤로 닫힌다
- URL 쿼리: `/?asset=<rel>` 이면 로드 후 최신 스냅샷을 선택하고 그 애셋의 버전 히스토리를 자동으로 연다, `/?view=status` 면 '현재 변경사항' 패널을 강조 (에디터 메뉴가 사용)
- 리세이브만(헤더만 바뀐) 스냅샷은 타임라인에서 흐리게 + '리세이브만' 배지로 표시되고 기본 접힘('리세이브 스냅샷 N개 숨김' 한 줄). 상단 '리세이브 스냅샷 보기' 토글(localStorage 기억)로 펼침. 목록에서 리세이브 항목은 `M~` 배지·흐린 행, 집계 줄에 '리세이브만 N', 카운트에 '리세이브 N' 별도 표기
- 포트: `.jokate/config.toml` 의 `[web] port`(기본 8765). `serve --port` 가 우선

## 에디터 메뉴 (콘텐츠 브라우저 우클릭 → Jokate)

`bridge-install` 후 에디터를 재시작하면(또는 Python 콘솔에서 `import jokate_bridge`) 콘텐츠 브라우저 애셋 우클릭 메뉴에 `Jokate` 서브메뉴가 생긴다. `python -m jokate serve <project>` 가 떠 있어야 동작한다 (안 떠 있으면 Output Log 에 '먼저 python -m jokate serve <프로젝트> 를 실행' 경고).

- `선택한 애셋 올리기(스냅샷)`: 선택 애셋만 부분 스냅샷. 메시지는 `에디터에서 올림: <애셋명> n개` 자동
- `선택한 애셋 올리기(스냅샷)`은 성공하면 로그만, 실패·변경 없음이면 알림 창
- `선택한 애셋을 마지막 스냅샷 상태로 되돌리기`: 웹과 같은 안전장치 — 먼저 `GET /api/restore/<HEAD>?asset=…` 드라이런을 받아 `Jokate 되돌리기` 확인창(변경 요약 `수정 n · 부활 n · 이동 n · 삭제 n`, 애셋 목록 최대 12줄, 참조 경고, '되돌리기 직전 안전 스냅샷이 자동 생성됩니다')을 띄운다. 바뀔 게 없으면 '이미 마지막 스냅샷 상태입니다' 창만. YES → `POST /api/restore/<HEAD>` → '롤백 완료 #N · 안전 스냅샷 #M · 복사 a · 삭제 b' 창. 409(저장 안 한 변경)면 dirty 목록과 함께 '저장하지 않은 변경을 버리고 진행할까요?' 를 묻고 YES 면 `discard_dirty` 로 재요청. 데몬이 꺼져 있으면 'start.bat 을 실행하거나 에디터를 다시 시작하세요' 창
- `히스토리 열기(웹)`: 브라우저로 `/?asset=<rel>` · `현재 변경사항 보기(웹)`: `/?view=status`
- 구현: `jokate/ue/jokate_client.py`(urllib 만, `unreal` 미사용 → `tests/test_ue_client.py` 로 가짜 서버 왕복 테스트) + `jokate_bridge.py` 의 `unreal.ToolMenuEntryScript` 서브클래스. 두 파일 모두 `bridge-install` 이 `Content/Python/` 에 복사
- HTTP 는 항상 `threading.Thread` 에서 보내고 결과는 큐 → 기존 `_tick` 에서 `unreal.log` + 모달 창(`unreal.EditorDialog.show_message`, `AppMsgType.OK/YES_NO`). 게임 스레드에서 동기로 부르면 서버가 같은 에디터의 브릿지(dirty/reload)를 기다리므로 데드락. 모달은 틱(게임 스레드)에서만 띄우고 창이 떠 있는 동안 큐 처리는 재진입하지 않는다
- 선택 애셋 → 패키지명 → `package_to_rel` (`/Game/A/B` → `A/B.uasset`, 디스크에 `.umap` 이 있으면 `.umap`). `/Game` 밖은 무시

## 자동 스냅샷 (watch)

- 외부 의존성 없이 폴링: authored 폴더의 `(size, mtime)` 맵을 `--interval` 초마다 비교. vendor/ignore 최상위 폴더는 아예 훑지 않는다
- 변화 감지 후 `--debounce` 초 동안 추가 변화가 없으면 auto 스냅샷 (에디터의 연속 저장을 한 스냅샷으로 묶음)
- 실제 변경 판단은 sha 비교. 리세이브로 mtime 만 바뀌면 `(내용 동일, 건너뜀)`
- 시작 시 즉시 스냅샷 한 번. 스냅샷마다 한 줄(시간, #id, 클래스별 A/M/R/D 집계)을 stdout 과 `.jokate/watch.log` 에 기록

## 정리 (squash · prune · gc)

- 스냅샷은 전부 '전체 트리'라서 중간 스냅샷을 지워도 남은 스냅샷은 온전하다. 지운 스냅샷을 부모로 가진 스냅샷은 살아남은 조상으로 다시 이어지고 `noise` 는 새 부모 기준으로 재계산된다. HEAD 는 절대 안 지운다
- `squash(ids, message, include_labels=False)`: 부모-자식으로 연속된 사슬만 허용(아니면 `ValueError`). 마지막 하나만 `kind=label` + 메시지로 남기고 나머지 삭제
- 사라질 쪽에 이름 붙인 스냅샷이 있으면 `SquashHasLabels`(`ValueError`, `.labels=[(id, message)]`) 로 막는다 — CLI `--include-labels`(막히면 exit 2), 웹은 409 `{labels}` 뒤 확인 후 `include_labels:true` 재요청
- `prune(auto_days, keep_last_auto, now, dry_run)`: `kind=auto` 이고 `auto_days` 보다 오래됐고 최신 auto `keep_last_auto` 개에 안 들고 HEAD 가 아닌 것만 삭제. label 과 '롤백 직전' 라벨 스냅샷은 보존('롤백 직전' 안전 스냅샷은 auto 라 같은 규칙으로 정리된다)
- `gc(dry_run)`: 어떤 tree 행도 참조하지 않는 객체 파일 삭제 + 빈 폴더·`.tmp` 잔여물 정리 → `(개수, 바이트)`. squash·prune 뒤에는 자동 실행. 모든 삭제는 한 트랜잭션
- 설정 `[retention] auto_days = 14`, `keep_last_auto = 30` (0 이면 정리 안 함)
- 데몬은 시작 시 한 번, 이후 24시간마다 prune+gc 를 돌리고 지운 게 있을 때만 `정리: 스냅샷 n개, 객체 m개 x MB` 를 `daemon.log` 에 남긴다
- 웹: `POST /api/squash {ids, message, include_labels}`, `POST /api/prune {dry_run}` → `{ids, objects, bytes}` (사슬 아님 등 `ValueError` 는 400)
- UI: 타임라인에서 Ctrl+클릭 토글 · Shift+클릭 범위 선택(accent 테두리), 우클릭 → '선택한 n개를 하나로 묶고 이름 붙이기'(연속 사슬일 때만 활성) / '이 스냅샷으로 되돌리기'. 통계 바 '저장소' 옆 '정리' 버튼은 먼저 dry_run 으로 확인 모달을 띄우고 지울 게 없으면 '정리할 것이 없습니다'

## 데몬 (daemon)

- 한 프로세스에서 웹 서버 스레드 + `poll_once` 감시 루프 스레드. 콘솔 창이 없다(`pythonw`) — 로그는 `.jokate/daemon.log`
- 상태 파일 `.jokate/daemon.json` `{pid, port, started, tool_dir}` — 시작 시 쓰고 종료 시 지운다. pid 가 살아 있고 그 포트의 `/api/info` 가 응답하면 '이미 실행 중' 으로 보고 새로 띄우지 않는다
- 웹 헤더의 상태 칩: `● 자동 스냅샷 켜짐` / `일시정지` 클릭 토글, 옆의 `종료` 버튼은 데몬을 끈다. `serve` 단독이면 회색 칩에 조작 불가(409)
- API: `GET /api/daemon`, `POST /api/daemon {action: pause|resume|stop}`

### 에디터를 켜면 자동 실행

- `bridge-install` 이 `.jokate/tool.json` `{tool_dir, python, pythonw, autostart}` 를 쓰고 `jokate_launch.py` 도 `Content/Python/` 에 복사한다. 도구를 다른 폴더로 옮기거나 파이썬을 바꿨으면 `bridge-install` 을 다시 실행
- 에디터가 뜨면 브릿지가 워커 스레드에서 `GET /api/daemon` 을 2초 타임아웃으로 확인 → 응답이 있으면 아무것도 안 하고(다만 `serve` 단독이면 '자동 스냅샷이 꺼져 있다' 경고), 연결 실패면 `pythonw -m jokate daemon <project>` 를 창 없이(DETACHED) 띄우고 Output Log 에 `[jokate] 데몬 자동 실행 pid=N`
- 자식 프로세스 환경에서 `PYTHON*` 변수(UE 의 `PYTHONHOME`/`PYTHONPATH` 등)를 모두 지운다. 안 지우면 시스템 파이썬이 UE 파이썬 경로로 오염되어 바로 죽는다
- 끄는 법: 환경변수 `JOKATE_NO_AUTOSTART=1`, 또는 `.jokate/config.toml` 의 `[editor] autostart = false` 후 `bridge-install` 재실행(값은 tool.json 에 기록된다)

### 트레이 아이콘

- 데몬이 시작할 때 `ctypes` 만으로 윈도우 트레이 아이콘(숨은 메시지 창 + `Shell_NotifyIconW`)을 자체 스레드에 띄운다. 툴팁 `Jokate - <프로젝트명>`
- 우클릭 메뉴: `타임라인 열기` / `지금 스냅샷` / `자동 스냅샷 일시정지·재개`(상태에 따라 라벨 변경) / `종료`. 더블클릭 = 타임라인 열기
- `지금 스냅샷` 은 `control.snap_now()` 로 플래그만 세우고 실제 스냅은 watch 루프 스레드가 찍는다(SQLite 스레드 고정)
- 최선 노력: Windows 가 아니거나 실패하면 `.jokate/daemon.log` 에 한 줄 남기고 데몬은 계속 돈다. 끄려면 `[daemon] tray = false`

## 롤백 (restore)

- 기본은 드라이런: 되돌릴 애셋(M 수정 되돌림 / A 부활 / R 이동 / D 삭제) 목록 + 클래스별 집계 + 참조 검산만 출력. 아무것도 바꾸지 않는다
- `--asset rel` 을 주면 그 애셋들만 스냅샷 시점으로, 나머지는 현재 상태 유지 (반복 가능)
- 참조 검산: 결과 트리 각 애셋의 `/Game/` 의존성이 결과 트리·vendor·현재 디스크 어디에도 없으면 "깨질 참조", 롤백으로 사라지는 애셋을 참조하는 authored 애셋은 별도 경고
- `--apply` 순서: ① auto 부분 스냅샷 `롤백 직전 #<id>` (되돌릴 애셋만, 안전망) ② 객체를 `Content/` 로 복사(tmp→replace) ③ 결과 트리에 없는 authored 파일 삭제 ④ label 부분 스냅샷 `롤백: #<id>` (되돌릴 애셋만)
- 두 스냅샷은 이번 롤백이 건드리는 rel(modified·부활·삭제·이동의 old/new)만 담는 부분 스냅샷이다 → 롤백과 무관한 애셋의 올리지 않은 변경은 롤백 뒤에도 '현재 변경사항' 에 그대로 남는다. 되돌릴 애셋의 디스크 상태가 HEAD 와 같으면 안전 스냅샷을 새로 만들지 않고 HEAD 를 직전 상태로 쓴다(`RestoreResult.safety_created=False`, API 응답·CLI·토스트는 '되돌리기 전 상태 #M')
- 객체 존재 검사는 실제로 디스크에 쓸 항목(복사 대상)에만 한다. 현재 상태 그대로 유지되는 항목은 객체가 없어도(수정만 하고 스냅샷 안 한 애셋) 부분 롤백이 통과한다. 대상 객체가 유실됐으면 아무것도 바꾸기 전에 `FileNotFoundError` (웹 API 409)
- `UnrealEditor.exe` 가 실행 중이면 에디터 브릿지가 필요하다 (아래). 브릿지가 없으면 `--apply` 거부
- 롤백도 되돌릴 수 있다: `restore <project> <안전 스냅샷 id> --apply`

## 에디터 브릿지 (bridge-install / bridge-status)

에디터가 켜진 상태에서도 롤백을 적용하기 위한 파일 기반 프로토콜. 외부 의존성 없음, `<project>/.jokate/bridge/`:

| 파일 | 쓰는 쪽 | 내용 |
|---|---|---|
| `heartbeat.json` | 에디터 | 1초마다 `{"ts"}` 갱신. 3초 넘게 오래되면 브릿지 없음으로 본다 |
| `request.json` | 도구 | `{"id","op","packages":["/Game/..."],"args"}` |
| `response-<id>.json` | 에디터 | `{"id","ok",...}` |

- `bridge-install <project>`: `jokate/ue/jokate_bridge.py` 를 `<project>/Content/Python/jokate_bridge.py` 로 복사하고 `init_unreal.py` 에 `import jokate_bridge` 한 줄을 보장 (없으면 생성, 있으면 그 줄이 없을 때만 추가). `.py` 는 애셋이 아니라 스캐너가 무시한다
- 에디터 쪽: `unreal.register_slate_post_tick_callback` 으로 1초마다 폴링. `dirty` → 요청 패키지 중 저장 안 된 것 목록. `reload` → dirty 대상이 있으면 `ok=false`(`args.discard_dirty` 면 통과), 존재하는 패키지는 `load_package` 후 `reload_packages(ASSUME_POSITIVE)`, 대상 폴더를 `scan_paths_synchronous(force_rescan)` 로 추가/삭제 반영. 예외는 `ok=false, error`
- `restore --apply` 는 에디터 실행 중이면: 브릿지 없음 → 거부 / `dirty` 요청 → 저장 안 된 대상이 있으면 목록 출력하고 중단(`--discard-dirty` 로 통과) / 파일 쓰기·삭제 후 `reload` 요청 → 실패면 에디터 재시작 안내. 에디터가 꺼져 있으면 파일만 바꾼다
- `bridge-status <project>`: heartbeat 나이와 생존 여부

## 스냅샷 저장소

- `.jokate/store/objects/<sha[:2]>/<sha>` — 원본 그대로(압축 없음), 내용주소(blake2b-256). 같은 내용은 한 번만 저장
- `.jokate/index.sqlite` — `snapshots(id, parent, kind, message, ts)`, `tree(snapshot_id, rel, sha, size, cls, deps)`
- 변경 판단은 mtime 이 아니라 sha 비교. 이동은 "sha 동일 + 경로 변경"으로 잡는다
- vendor 등급은 스냅샷에 포함하지 않는다

## 의미 diff (DataTable 값 비교)

- 웹 UI 스냅샷 상세에서 클래스가 `DataTable` 인 수정 행을 클릭하면 버전 히스토리 위에 **내용 변경** 표가 뜬다 (예: 공격력 `30 → 45`, 추가된 행은 초록, 삭제된 행은 빨강, 열 추가·삭제는 표 위 필, 값이 같은 행은 "변경 없는 행 n개")
- **무엇이 기록되나**: `<project>/.jokate/store/meta/<sha[:2]>/<sha>.json` = `{kind, row_struct, columns, rows:{행이름:{열:"값"}}}` — 값은 모두 문자열
- **언제 기록되나**: 스냅샷을 찍는 순간(데몬 자동·웹 올리기·CLI `snap`) 에디터 브릿지가 살아 있으면, 그때 에디터에 로드된 **현재 버전**을 `export_meta` 로 받아 그 파일의 sha 를 키로 저장한다. 과거 `.uasset` 을 다시 로드하지 않는다
- **한계**: 에디터가 꺼져 있거나 브릿지가 없으면 그 버전은 기록이 없다(UI 안내만 표시). 대상 패키지가 저장 안 된(dirty) 상태면 건너뛴다. 응답을 받은 뒤 파일 sha 가 달라졌으면 그 기록은 버린다. 두 버전 모두 사이드카가 있어야 비교되므로, 이 기능이 들어간 뒤 찍힌 스냅샷부터 의미 있다. DataTable 외 클래스는 아직 대상이 아니다
- `gc` 는 어떤 스냅샷도 참조하지 않는 sha 의 사이드카도 함께 지운다

## UE 에서 diff 열기

- 블루프린트처럼 우리 UI 로는 못 보는 애셋은 **언리얼 에디터의 diff 창**으로 두 버전을 나란히 본다
- 웹 UI: 버전 히스토리 헤더의 `⇄ UE 에서 diff 열기`(선택한 스냅샷 버전 ↔ 직전 버전), 카드 우클릭 → *직전 버전과 비교* / *현재 파일과 비교*
- CLI: `python -m jokate uediff <project> <rel> <id_a> [<id_b>]` — `id_b` 를 빼면 작업 트리의 현재 파일과 비교
- 동작: 두 버전을 `<project>/.jokate/tmp/diff/<이름>__<sha8>.<확장자>` 로 꺼낸 뒤
  `UnrealEditor.exe <프로젝트.uproject> -diff <왼쪽> <오른쪽>` 을 창 분리로 실행한다(에디터가 하나 더 뜨고 1분쯤 걸린다). 임시 파일은 24시간 뒤 자동 정리
- 에디터 경로: ① `.jokate/config.toml` 의 `[editor] exe` ② `.uproject` 의 `EngineAssociation` — 버전(`5.7`)이면 `HKLM\SOFTWARE\EpicGames\Unreal Engine\<버전>` 의 `InstalledDirectory`, GUID(소스 빌드)면 `HKCU\Software\Epic Games\Unreal Engine\Builds` 의 값
- 못 찾으면 웹은 409 로 안내 모달을 띄운다 → `[editor] exe = "D:/UE_5.7/Engine/Binaries/Win64/UnrealEditor.exe"` 처럼 적어 주면 된다

## noise(리세이브) 판정

- 에디터가 내용 변경 없이 다시 저장하면 SavedHash·엔진 버전·썸네일·오프셋 등 헤더만 바뀌어 sha 가 달라진다
- `snap` 시 HEAD 대비 sha 가 바뀐 애셋마다 `uasset.is_resave_only(HEAD 객체, 현재 파일)` 로 판정: 이름·임포트·익스포트 표가 같고 각 export 직렬화 바이트가 동일하면 `tree.noise=1` (파싱 실패·예외는 0)
- `Diff.resave` / `real_modified` / `all_noise`, `by_class()` 는 noise 항목을 `modified` 대신 `resave` 로 집계. 변경 여부(`empty`) 판정은 그대로(리세이브도 스냅샷은 만든다)
- 표시: `status`/`show` 는 `M~ rel [cls] (리세이브만)`, `log` 는 스냅샷 전체가 리세이브면 줄 끝에 `(리세이브만)`, `watch` 는 `리세이브만 N`
- 옛 DB 는 `Store` 를 열 때 `ALTER TABLE tree ADD COLUMN noise` 로 자동 마이그레이션(기존 행 0)

## 구성

- `jokate/uasset.py` — 패키지 헤더 파서 (요약·이름·임포트·익스포트·썸네일). UE 4.11 ~ 5.7 검증
- `jokate/scan.py` — 등급 분류 + 애셋 레코드(클래스·부모·의존성·해시)
- `jokate/store.py` — 스냅샷 저장소 (내용주소 객체 + SQLite 인덱스, 트리 diff)
- `jokate/watch.py` — 저장 감지 자동 스냅샷 데몬 (폴링 + debounce, `poll_once` 순수 함수)
- `jokate/daemon.py` — 창 없는 단일 데몬 (웹 + watch 스레드, 상태 파일·로그, pause/resume/stop 컨트롤)
- `jokate/web.py` + `jokate/web_static/index.html` — 타임라인 웹 UI (JSON API 는 `api_*` 순수 함수, 서버 없이 테스트)
- `jokate/bridge.py` — 에디터 브릿지 도구 쪽 (`bridge_alive`, `request`, `install`) / `jokate/ue/jokate_bridge.py` — 에디터 쪽 (UE Python, `import unreal`, 콘텐츠 브라우저 메뉴) / `jokate/ue/jokate_client.py` — 웹 API 클라이언트 (urllib 만) / `jokate/ue/jokate_launch.py` — 데몬 실행기 (tool.json, PYTHON* 청소, 창 없는 Popen) / `jokate/tray.py` — 윈도우 트레이 아이콘 (ctypes, `menu_items` 순수 함수)
- `jokate/config.py` — 프로젝트 설정
- `jokate/__main__.py` — CLI

## 로드맵

1. ~~헤더 파서 + 스캐너~~
2. ~~스냅샷 저장소 (내용주소 + SQLite) + 타임라인 웹 UI~~
3. ~~롤백 (시점 전체 복귀 / 애셋 단위, 드라이런 + 안전 스냅샷, 참조 검산)~~ → 애셋 히스토리, UE Python 브릿지
4. ~~저장 감지 자동 스냅샷 데몬 (watch)~~ → 리세이브 noise 필터, rename 추적
