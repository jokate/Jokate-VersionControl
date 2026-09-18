# HTTP API

`python -m jokate serve <project> [--port 8765]` 또는 `daemon` 이 띄우는 로컬 서버(`127.0.0.1`). 표준 라이브러리 `http.server` 만 사용하고, 응답은 모두 `application/json; charset=utf-8`(썸네일 제외)이다. 핸들러 로직은 `jokate/web.py` 의 `api_*` 순수 함수로 분리돼 서버 없이 테스트한다.

## GET

| 메서드·경로 | 요청 | 응답 | 오류 |
|---|---|---|---|
| `GET /` | — | `web_static/index.html` | 404 |
| `GET /api/info` | — | 프로젝트명, 스냅샷 수, HEAD 추적 애셋 수, 객체 수·용량, 마지막 스냅샷 + `build`(서버 시작 시 코드 해시)·`build_disk`(현재 디스크, 5초 캐시)·`stale`(둘이 다름) + `pending`(아직 올리지 않은 변경 수)·`last_label`(마지막으로 올린 스냅샷)·`vendor`(동결 폴더 glob 목록, UI 가 참조 변화에 vendor 표시를 붙일 때 씀). UI 는 5초마다 이걸 폴링해 타임라인·변경사항을 갱신한다 | — |
| `GET /api/log` | — | 스냅샷 목록 + 변경 건수·클래스별 집계. 사용자가 올린 스냅샷은 `uploaded:true` 이고 집계가 '이번에 올린 항목' 기준 | — |
| `GET /api/snap/<id>` | 경로 id | `show` 와 동일한 diff (A/M/R/D + `by_class` + `all_noise`). `modified`·`moved` 항목마다 참조 변화 `deps_added`·`deps_removed`(정렬된 `/Game/...` 목록)·`deps_missing`(추가분 중 그 시점 트리에 없는 패키지) | 404 없는 id |
| `GET /api/asset` | `rel` | 애셋 버전 히스토리(스냅샷별 sha·size·변경여부 + 직전 버전 대비 `deps_added`·`deps_removed`, 최신순) | 400 `rel` 없음 |
| `GET /api/thumb` | `sha` 또는 `rel` | `image/jpeg`\|`image/png` 바이트 | 404 썸네일 없음·경로 탈출 |
| `GET /api/search` | `q` | 애셋·클래스·메시지 부분일치(대소문자 무시) 스냅샷 + 일치 애셋 | — |
| `GET /api/metadiff` | `a`, `b` (sha) | `{available, missing, kind, diff}` DataTable 값 diff | — |
| `GET /api/restore/<id>` | `asset`(반복 가능) | `plan_restore` 드라이런 `{diff, broken, dependents}`. 적용 없음 | 404 없는 id |
| `GET /api/status` | — | `{diff}` baseline(마지막으로 올린 상태) 대비 아직 올리지 않은 변경 — 자동 스냅샷이 쌓여도 비지 않는다 | — |
| `GET /api/revert` | `asset`(반복 가능) | baseline 으로 되돌리기 드라이런 `{snapshot(id=0), diff, broken, dependents}`. 적용 없음 | — |
| `GET /api/daemon` | — | `{running, paused, pid, port, started, last_line}` | — |
| `GET /api/uediff/plan` | — | `{ok, mode:"editor"\|"process", editor_running, bridge, hint}` — diff 를 열면 어떤 방식이 될지 미리 알려준다(UI 는 `process` 면 '에디터를 새로 띄울까요?' 확인 모달을 먼저 띄운다) | — |

## POST

요청 본문은 JSON.

| 메서드·경로 | 요청 | 응답 | 오류 |
|---|---|---|---|
| `POST /api/daemon` | `{action: "pause"\|"resume"\|"stop"\|"restart"}` | 갱신된 데몬 상태(`restart` 는 `restarted_pid` 포함 — 새 데몬을 띄우고 자신은 종료) | 409 데몬 모드가 아님(`serve` 단독) |
| `POST /api/snap` | `{message, only?:[rel]}` | 만들어진 label 스냅샷(`only` 면 부분 스냅샷) | 400 `message` 없음·`only` 형식 |
| `POST /api/restore/<id>` | `{assets?:[rel], discard_dirty?:bool}` | `{ok:true, safety, result, written, deleted, safety_created}` | 409 dirty·브릿지 없음 `{ok:false, error, dirty:[...]}`, 409 객체 유실, 500 그 외 |
| `POST /api/revert` | `{assets?:[rel], discard_dirty?:bool}` | baseline 으로 되돌리기 적용 — 응답·409 규칙은 `POST /api/restore/<id>` 와 동일 | 409 dirty·브릿지 없음·잠김 `{ok:false, error, dirty, locked}` |
| `POST /api/squash` | `{ids:[id], message, include_labels?}` | 남은 스냅샷 | 400 연속 사슬 아님, 409 사라질 쪽에 라벨 `{labels}` |
| `POST /api/uediff` | `{rel, a, b?}` | `{ok, mode:"editor"\|"process", strategy, note, pid, left, right}` — 에디터가 켜져 있으면 **반드시** 그 에디터에서 열고, 꺼져 있을 때만 새 에디터 프로세스(`b` 없으면 현재 파일과 비교) | 400 `rel`/`a` 없음, 409 에디터 못 찾음 `{ok:false, error}`, 409 에디터는 켜져 있는데 브릿지 꺼짐·op 실패 `{ok:false, error, mode:"editor"}` |
| `POST /api/prune` | `{dry_run:bool}` | `{ids, objects, bytes}` (정리 + GC) | — |

오류 응답은 공통으로 `{ok:false, error:"..."}` 형태이며, `ValueError` 계열은 400, 없는 경로·대상은 404, 진행이 막힌 경우(`RestoreBlocked`, `SquashHasLabels`, 객체 유실, 데몬 아님)는 409, 나머지는 500 이다.

## URL 쿼리 딥링크

웹 UI(`/`)는 다음 쿼리를 이해한다. 에디터 우클릭 메뉴가 사용한다.

| 쿼리 | 동작 |
|---|---|
| `/?asset=<rel>` | 로드 후 최신 스냅샷을 선택하고 그 애셋의 버전 히스토리를 연다 |
| `/?view=status` | '현재 변경사항' 패널을 강조한다 |
