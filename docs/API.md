# HTTP API

`python -m jokate serve <project> [--port 8765]` 또는 `daemon` 이 띄우는 로컬 서버(`127.0.0.1`). 표준 라이브러리 `http.server` 만 사용하고, 응답은 모두 `application/json; charset=utf-8`(썸네일 제외)이다. 핸들러 로직은 `jokate/web.py` 의 `api_*` 순수 함수로 분리돼 서버 없이 테스트한다.

## GET

| 메서드·경로 | 요청 | 응답 | 오류 |
|---|---|---|---|
| `GET /` | — | `web_static/index.html` | 404 |
| `GET /api/info` | — | 프로젝트명, 스냅샷 수, HEAD 추적 애셋 수, 객체 수·용량, 마지막 스냅샷 + `build`(서버 시작 시 코드 해시)·`build_disk`(현재 디스크, 5초 캐시)·`stale`(둘이 다름) + `pending`(아직 올리지 않은 변경 수)·`last_label`(마지막으로 올린 스냅샷)·`vendor`(동결 폴더 glob 목록, UI 가 참조 변화에 vendor 표시를 붙일 때 씀) + `fixes`(확정 버전 수)·`journal`(작업 중 기록 수). UI 는 5초마다 이걸 폴링해 확정 이력·확정 안 된 변경·작업 중 기록을 갱신한다 | — |
| `GET /api/log` | `role=fix\|journal\|all`(기본 all) | 스냅샷 목록 + 변경 건수·클래스별 집계 + `role`. `fix`=확정 버전(웹 UI '확정 이력'), `journal`=작업 중 기록(웹 UI '작업 중 기록' 서랍). 확정한 스냅샷은 `uploaded:true` 이고 집계가 '이번에 확정한 항목' 기준 | — |
| `GET /api/snap/<id>` | 경로 id | `show` 와 동일한 diff (A/M/R/D + `by_class` + `all_noise`). `modified`·`moved` 항목마다 참조 변화 `deps_added`·`deps_removed`(정렬된 `/Game/...` 목록)·`deps_missing`(추가분 중 그 시점 트리에 없는 패키지) | 404 없는 id |
| `GET /api/asset` | `rel` | 애셋 버전 히스토리(스냅샷별 sha·size·변경여부 + 직전 버전 대비 `deps_added`·`deps_removed`, 최신순) | 400 `rel` 없음 |
| `GET /api/thumb` | `sha` 또는 `rel` | `image/jpeg`\|`image/png` 바이트 | 404 썸네일 없음·경로 탈출 |
| `GET /api/search` | `q` | 애셋·클래스·메시지 부분일치(대소문자 무시) 스냅샷 + 일치 애셋 | — |
| `GET /api/metadiff` | `a`, `b` (sha) | `{available, missing, kind, diff}` — kind `DataTable` 은 표 diff, `Text` 는 `{cls, props, diff:{lines,added,removed,truncated}}` | — |
| `GET /api/restore/<id>` | `asset`(반복 가능) | `plan_restore` 드라이런 `{diff, broken, dependents}`. 적용 없음 | 404 없는 id |
| `GET /api/status` | — | `{diff}` baseline(마지막으로 올린 상태) 대비 아직 올리지 않은 변경 — 자동 스냅샷이 쌓여도 비지 않는다 | — |
| `GET /api/revert` · `GET /api/discard` | `asset`(반복 가능) | 변경 버리기(마지막 확정 상태로) 드라이런 `{snapshot(id=0), diff, broken, dependents}`. 적용 없음 | — |
| `GET /api/daemon` | — | `{running, paused, pid, port, started, last_line}` | — |
| `GET /api/uediff/plan` | — | `{ok, mode:"editor"\|"process", editor_running, bridge, hint}` — diff 를 열면 어떤 방식이 될지 미리 알려준다(UI 는 `process` 면 '에디터를 새로 띄울까요?' 확인 모달을 먼저 띄운다) | — |

## POST

요청 본문은 JSON.

| 메서드·경로 | 요청 | 응답 | 오류 |
|---|---|---|---|
| `POST /api/daemon` | `{action: "pause"\|"resume"\|"stop"\|"restart"}` | 갱신된 데몬 상태(`restart` 는 `restarted_pid` 포함 — 새 데몬을 띄우고 자신은 종료) | 409 데몬 모드가 아님(`serve` 단독) |
| `POST /api/confirm` · `POST /api/snap` | `{message, only?:[rel]}` | 확정. 만들어진 확정 버전(`only` 면 고른 애셋만) + `cleared`(이번 확정이 지운 작업 중 기록 수). 확정할 게 없으면 `snapshot:null` | 400 `message` 없음·`only` 형식 |
| `POST /api/restore/<id>` | `{assets?:[rel], discard_dirty?:bool}` | `{ok:true, safety, result, written, deleted, safety_created}` | 409 dirty·브릿지 없음 `{ok:false, error, dirty:[...]}`, 409 객체 유실, 500 그 외 |
| `POST /api/revert` · `POST /api/discard` | `{assets?:[rel], discard_dirty?:bool}` | 변경 버리기 적용 — 응답은 `POST /api/restore/<id>` 와 같고 `undo`(정리 뒤에도 남는 실행 취소 지점 스냅샷, 없으면 `null`)·`cleared`(정리한 작업 중 기록 수)가 붙는다. `undo` 는 새로 만든 안전 스냅샷이거나, 같은 내용을 담은 기존 확정 버전이다 | 409 dirty·브릿지 없음·잠김 `{ok:false, error, dirty, locked}` |
| `POST /api/squash` | `{ids:[id], message, include_labels?}` | 남은 스냅샷 | 400 연속 사슬 아님, 409 사라질 쪽에 라벨 `{labels}` |
| `POST /api/uediff` | `{rel, a, b?}` | `{ok, mode:"editor"\|"process", strategy, note, pid, left, right}` — 에디터가 켜져 있으면 **반드시** 그 에디터에서 열고, 꺼져 있을 때만 새 에디터 프로세스(`b` 없으면 현재 파일과 비교) | 400 `rel`/`a` 없음, 409 에디터 못 찾음 `{ok:false, error}`, 409 에디터는 켜져 있는데 브릿지 꺼짐·op 실패 `{ok:false, error, mode:"editor"}` |
| `POST /api/prune` | `{dry_run:bool}` | `{ids, objects, bytes}` (정리 + GC) | — |

## UE 프로바이더용 (리비전 컨트롤 플러그인)

언리얼 에디터의 리비전 컨트롤 프로바이더(`ISourceControlProvider`)가 쓰는 엔드포인트. 오류 규칙은 위와 같다.

| 메서드·경로 | 요청 | 응답 | 오류 |
|---|---|---|---|
| `GET /api/ping` | — | `{ok:true, project, root, content, port, build, api:1}` — 이 데몬이 내 프로젝트의 것인지 확인 (`root`·`content` 는 슬래시 절대경로) | — |
| `POST /api/states` | `{rels?:[rel]}` (비면 추적 대상 전체) | `{head_fix:{id,message,time}\|null, states:{rel:{state, tier, sha, baseline_sha, size, cls, noise}}}` | 400 `rels` 형식 |
| `GET /api/history?rel=<rel>&limit=50` | — | 그 애셋의 **확정 버전** 이력만(작업 중 기록 제외), 최신순 `[{id, revision, message, ts, time, sha, size, action:"add"\|"edit"\|"delete"}]` | 400 `rel` 없음 |
| `POST /api/extract` | `{rel, sha}` | `{ok:true, path}` — `<project>/Saved/JokateDiff/<sha8>/<이름>` 에 풀어 둔 절대경로(슬래시). 이미 있으면 재사용 | 400 sha 형식, 404 객체 없음 |

`state` 값은 baseline(마지막 확정 상태) 기준이다 — 자동 스냅샷이 찍혀도 바뀌지 않는다.

| 값 | 뜻 |
|---|---|
| `clean` | baseline 과 동일 |
| `modified` | baseline 에 있고 내용이 다름 (`noise:true` 면 리세이브만) |
| `added` | baseline 에 없고 디스크에 있음 |
| `deleted` | baseline 에 있고 디스크에 없음 |
| `untracked` | vendor 등급·ignore·Content 밖(경로 탈출)·애셋 확장자 아님 |
| `missing` | baseline 에도 디스크에도 없음 |

이동은 `added` + `deleted` 로 풀어서 표현한다. `rel` 은 Content 기준 슬래시 경로(역슬래시·선행 슬래시는 정규화).

`POST /api/confirm` 과 `POST /api/discard` 는 `editor_managed:bool` 을 추가로 받는다. `true` 면 서버는
에디터 브릿지 호출(dirty 확인·release·reload)을 전혀 하지 않는다 — 언리얼의 리비전 컨트롤 흐름이
패키지 언로드·리로드를 직접 하기 때문이다. 사전 잠금 검사·replace 재시도·tmp 정리는 그대로 동작한다.

오류 응답은 공통으로 `{ok:false, error:"..."}` 형태이며, `ValueError` 계열은 400, 없는 경로·대상은 404, 진행이 막힌 경우(`RestoreBlocked`, `SquashHasLabels`, 객체 유실, 데몬 아님)는 409, 나머지는 500 이다.

## URL 쿼리 딥링크

웹 UI(`/`)는 다음 쿼리를 이해한다. 에디터 우클릭 메뉴가 사용한다.

| 쿼리 | 동작 |
|---|---|
| `/?asset=<rel>` | 로드 후 최신 스냅샷을 선택하고 그 애셋의 버전 히스토리를 연다 |
| `/?view=status` | '확정 안 된 변경' 카드를 강조한다 |
