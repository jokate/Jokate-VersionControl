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
python -m jokate bridge-install <project>        # 에디터 브릿지 스크립트를 Content/Python 에 설치
python -m jokate bridge-status  <project>        # 브릿지 heartbeat 나이
python -m jokate watch   <project> [--interval 2] [--debounce 5]      # 저장 감지 자동 스냅샷 데몬 (Ctrl+C 종료)
python -m jokate serve   <project> [--port 8765]                      # 타임라인 웹 UI (http://127.0.0.1:8765/)
```

## 배치 파일 (더블클릭)

경로를 타이핑하지 않는다: 처음 한 번 `.uproject` 파일 선택 창이 뜨고 `project.local.txt`(gitignore)에 기억한다. `.uproject` 를 bat 위에 끌어다 놓아도 된다.

| 파일 | 하는 일 |
|---|---|
| `start.bat` | 자동 스냅샷(`watch`, 최소화 창) + 타임라인 웹 UI(`serve`) + 브라우저 열기. 이미 떠 있으면 브라우저만. Ctrl+C 로 끄면 watch 창도 닫힘. `JOKATE_NO_WATCH=1` / `JOKATE_NO_BROWSER=1` |
| `snap.bat` | 올리지 않은 변경을 보여주고 메시지를 받아 라벨 스냅샷 (비우면 취소) |
| `bridge-install.bat` | 에디터 브릿지 + 콘텐츠 브라우저 Jokate 메뉴 설치 |
| `change-project.bat` | 기억한 프로젝트를 지우고 다시 선택 |
| `_project.bat`, `tools/pick_project.ps1` | 공용 도우미 (직접 실행하지 않음). 처음 쓰는 프로젝트면 `init` 까지 실행 |

## 타임라인 웹 UI (serve)

- 표준 라이브러리 `http.server` 만 사용, 프레임워크 없음. 단일 HTML + vanilla JS, 다크 테마, 한국어
- 왼쪽 타임라인(label 진하게 / auto 흐리게, `수정 3 · 추가 1` 건수 + 클래스 배지), 오른쪽 선택 스냅샷의 A/M/R/D 목록
- 목록 항목을 클릭하면 하단에 그 애셋의 버전 히스토리 + 버전별 썸네일(패키지 헤더의 첫 썸네일)
- 상단 메시지 입력 + '스냅샷 만들기'(label), 왼쪽 상단 '현재 변경사항' 패널(HEAD 대비 아직 올리지 않은 A/M/R/D, 30초 자동 갱신), 우클릭 메뉴로 부분 올리기·되돌리기 (아래 'UI 조작')
- JSON API: `GET /api/log`, `GET /api/status`, `GET /api/snap/<id>`, `GET /api/asset?rel=`, `GET /api/thumb?sha=`, `GET /api/restore/<id>[?asset=]`, `POST /api/snap {message, only?:[rel]}`, `POST /api/restore/<id> {assets?:[rel], discard_dirty?:bool}`
- `POST /api/restore/<id>` 는 plan_restore→apply_restore 실행. 성공 `{ok:true, safety, result, written, deleted}`; 에디터 dirty·브릿지 없음으로 중단되면 409 `{ok:false, error, dirty:[...]}` (`store.RestoreBlocked`), 그 외 500.

## UI 조작

- 선택: 애셋 목록(현재 변경사항 패널, 스냅샷 상세)에서 클릭 토글 · Shift 범위 · Ctrl 추가 · 헤더 체크박스 전체 선택/해제 · Ctrl+A 전체 · Esc 해제. 선택 수는 헤더 옆에 표시
- 현재 변경사항 패널 우클릭: '선택한 것만 올리기'(메시지 모달 → `POST /api/snap {only}`), '전부 올리기', '선택한 것 되돌리기 — 마지막 스냅샷 상태로'
- 스냅샷 상세 우클릭: '선택한 애셋을 이 시점으로 되돌리기', '이 스냅샷 전체로 되돌리기'. 애셋 히스토리 카드 우클릭: '이 버전으로 되돌리기'
- 되돌리기는 항상 드라이런 확인 모달을 먼저 띄운다: 변경 목록(M/A/R/D) + 클래스별 집계 + 참조 경고(빨강) + '되돌리기 직전 안전 스냅샷이 자동 생성됩니다'. 확인하면 `POST /api/restore/<id>` 적용 → '롤백 완료 #N · 안전 스냅샷 #M' 토스트, 타임라인·변경사항 갱신
- 에디터에 저장 안 한 변경(dirty)으로 409 가 오면 모달에 dirty 목록과 '저장 안 한 변경 버리고 진행' 버튼(`discard_dirty:true` 재요청). 그 외 오류는 모달에 메시지
- 컨텍스트 메뉴는 화면 밖으로 나가지 않으며 Esc·바깥 클릭·스크롤로 닫힌다
- URL 쿼리: `/?asset=<rel>` 이면 로드 후 최신 스냅샷을 선택하고 그 애셋의 버전 히스토리를 자동으로 연다, `/?view=status` 면 '현재 변경사항' 패널을 강조 (에디터 메뉴가 사용)
- 리세이브만(헤더만 바뀐) 스냅샷은 타임라인에서 흐리게 + '리세이브만' 배지로 표시되고 기본 접힘('리세이브 스냅샷 N개 숨김' 한 줄). 상단 '리세이브 스냅샷 보기' 토글(localStorage 기억)로 펼침. 목록에서 리세이브 항목은 `M~` 배지·흐린 행, 집계 줄에 '리세이브만 N', 카운트에 '리세이브 N' 별도 표기
- 포트: `.jokate/config.toml` 의 `[web] port`(기본 8765). `serve --port` 가 우선

## 에디터 메뉴 (콘텐츠 브라우저 우클릭 → Jokate)

`bridge-install` 후 에디터를 재시작하면(또는 Python 콘솔에서 `import jokate_bridge`) 콘텐츠 브라우저 애셋 우클릭 메뉴에 `Jokate` 서브메뉴가 생긴다. `python -m jokate serve <project>` 가 떠 있어야 동작한다 (안 떠 있으면 Output Log 에 '먼저 python -m jokate serve <프로젝트> 를 실행' 경고).

- `선택한 애셋 올리기(스냅샷)`: 선택 애셋만 부분 스냅샷. 메시지는 `에디터에서 올림: <애셋명> n개` 자동
- `선택한 애셋을 마지막 스냅샷 상태로 되돌리기`: `POST /api/restore/<HEAD>` (드라이런 없음, 안전 스냅샷은 서버가 자동 생성). 에디터에 저장 안 한 대상이 있으면 409 로 차단 → Output Log 에 dirty 목록과 '저장 후 다시 시도'. 성공 시 '롤백 완료 #N'
- `히스토리 열기(웹)`: 브라우저로 `/?asset=<rel>` · `현재 변경사항 보기(웹)`: `/?view=status`
- 구현: `jokate/ue/jokate_client.py`(urllib 만, `unreal` 미사용 → `tests/test_ue_client.py` 로 가짜 서버 왕복 테스트) + `jokate_bridge.py` 의 `unreal.ToolMenuEntryScript` 서브클래스. 두 파일 모두 `bridge-install` 이 `Content/Python/` 에 복사
- HTTP 는 항상 `threading.Thread` 에서 보내고 결과는 큐 → 기존 `_tick` 에서 `unreal.log` 로 보고. 게임 스레드에서 동기로 부르면 서버가 같은 에디터의 브릿지(dirty/reload)를 기다리므로 데드락
- 선택 애셋 → 패키지명 → `package_to_rel` (`/Game/A/B` → `A/B.uasset`, 디스크에 `.umap` 이 있으면 `.umap`). `/Game` 밖은 무시

## 자동 스냅샷 (watch)

- 외부 의존성 없이 폴링: authored 폴더의 `(size, mtime)` 맵을 `--interval` 초마다 비교. vendor/ignore 최상위 폴더는 아예 훑지 않는다
- 변화 감지 후 `--debounce` 초 동안 추가 변화가 없으면 auto 스냅샷 (에디터의 연속 저장을 한 스냅샷으로 묶음)
- 실제 변경 판단은 sha 비교. 리세이브로 mtime 만 바뀌면 `(내용 동일, 건너뜀)`
- 시작 시 즉시 스냅샷 한 번. 스냅샷마다 한 줄(시간, #id, 클래스별 A/M/R/D 집계)을 stdout 과 `.jokate/watch.log` 에 기록

## 롤백 (restore)

- 기본은 드라이런: 되돌릴 애셋(M 수정 되돌림 / A 부활 / R 이동 / D 삭제) 목록 + 클래스별 집계 + 참조 검산만 출력. 아무것도 바꾸지 않는다
- `--asset rel` 을 주면 그 애셋들만 스냅샷 시점으로, 나머지는 현재 상태 유지 (반복 가능)
- 참조 검산: 결과 트리 각 애셋의 `/Game/` 의존성이 결과 트리·vendor·현재 디스크 어디에도 없으면 "깨질 참조", 롤백으로 사라지는 애셋을 참조하는 authored 애셋은 별도 경고
- `--apply` 순서: ① auto 스냅샷 `롤백 직전 #<id>` (안전망, 변경 없어도 생성) ② 객체를 `Content/` 로 복사(tmp→replace) ③ 결과 트리에 없는 authored 파일 삭제 ④ label 스냅샷 `롤백: #<id>`
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
- `jokate/web.py` + `jokate/web_static/index.html` — 타임라인 웹 UI (JSON API 는 `api_*` 순수 함수, 서버 없이 테스트)
- `jokate/bridge.py` — 에디터 브릿지 도구 쪽 (`bridge_alive`, `request`, `install`) / `jokate/ue/jokate_bridge.py` — 에디터 쪽 (UE Python, `import unreal`, 콘텐츠 브라우저 메뉴) / `jokate/ue/jokate_client.py` — 웹 API 클라이언트 (urllib 만)
- `jokate/config.py` — 프로젝트 설정
- `jokate/__main__.py` — CLI

## 로드맵

1. ~~헤더 파서 + 스캐너~~
2. ~~스냅샷 저장소 (내용주소 + SQLite) + 타임라인 웹 UI~~
3. ~~롤백 (시점 전체 복귀 / 애셋 단위, 드라이런 + 안전 스냅샷, 참조 검산)~~ → 애셋 히스토리, UE Python 브릿지
4. ~~저장 감지 자동 스냅샷 데몬 (watch)~~ → 리세이브 noise 필터, rename 추적
