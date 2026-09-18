# HTTP API

`python -m jokate serve <project> [--port 8765]` 또는 `daemon` 이 띄우는 로컬 서버(`127.0.0.1`). 표준 라이브러리 `http.server` 만 사용하고, 응답은 모두 `application/json; charset=utf-8`(썸네일 제외)이다. 핸들러 로직은 `jokate/web.py` 의 `api_*` 순수 함수로 분리돼 서버 없이 테스트한다.

## GET

| 메서드·경로 | 요청 | 응답 | 오류 |
|---|---|---|---|
| `GET /` | — | `web_static/index.html` | 404 |
| `GET /api/info` | — | 프로젝트명, 스냅샷 수, HEAD 추적 애셋 수, 객체 수·용량, 마지막 스냅샷 | — |
| `GET /api/log` | — | 스냅샷 목록 + 변경 건수·클래스별 집계 | — |
| `GET /api/snap/<id>` | 경로 id | `show` 와 동일한 diff (A/M/R/D + `by_class` + `all_noise`) | 404 없는 id |
| `GET /api/asset` | `rel` | 애셋 버전 히스토리(스냅샷별 sha·size·변경여부, 최신순) | 400 `rel` 없음 |
| `GET /api/thumb` | `sha` 또는 `rel` | `image/jpeg`\|`image/png` 바이트 | 404 썸네일 없음·경로 탈출 |
| `GET /api/search` | `q` | 애셋·클래스·메시지 부분일치(대소문자 무시) 스냅샷 + 일치 애셋 | — |
| `GET /api/metadiff` | `a`, `b` (sha) | `{available, missing, kind, diff}` DataTable 값 diff | — |
| `GET /api/restore/<id>` | `asset`(반복 가능) | `plan_restore` 드라이런 `{diff, broken, dependents}`. 적용 없음 | 404 없는 id |
| `GET /api/status` | — | `{diff}` HEAD 대비 아직 올리지 않은 변경 | — |
| `GET /api/daemon` | — | `{running, paused, pid, port, started, last_line}` | — |

## POST

요청 본문은 JSON.

| 메서드·경로 | 요청 | 응답 | 오류 |
|---|---|---|---|
| `POST /api/daemon` | `{action: "pause"\|"resume"\|"stop"}` | 갱신된 데몬 상태 | 409 데몬 모드가 아님(`serve` 단독) |
| `POST /api/snap` | `{message, only?:[rel]}` | 만들어진 label 스냅샷(`only` 면 부분 스냅샷) | 400 `message` 없음·`only` 형식 |
| `POST /api/restore/<id>` | `{assets?:[rel], discard_dirty?:bool}` | `{ok:true, safety, result, written, deleted, safety_created}` | 409 dirty·브릿지 없음 `{ok:false, error, dirty:[...]}`, 409 객체 유실, 500 그 외 |
| `POST /api/squash` | `{ids:[id], message, include_labels?}` | 남은 스냅샷 | 400 연속 사슬 아님, 409 사라질 쪽에 라벨 `{labels}` |
| `POST /api/uediff` | `{rel, a, b?}` | 실행 결과(`b` 없으면 현재 파일과 비교) | 400 `rel`/`a` 없음, 409 에디터 못 찾음 `{ok:false, error}` |
| `POST /api/prune` | `{dry_run:bool}` | `{ids, objects, bytes}` (정리 + GC) | — |

오류 응답은 공통으로 `{ok:false, error:"..."}` 형태이며, `ValueError` 계열은 400, 없는 경로·대상은 404, 진행이 막힌 경우(`RestoreBlocked`, `SquashHasLabels`, 객체 유실, 데몬 아님)는 409, 나머지는 500 이다.

## URL 쿼리 딥링크

웹 UI(`/`)는 다음 쿼리를 이해한다. 에디터 우클릭 메뉴가 사용한다.

| 쿼리 | 동작 |
|---|---|
| `/?asset=<rel>` | 로드 후 최신 스냅샷을 선택하고 그 애셋의 버전 히스토리를 연다 |
| `/?view=status` | '현재 변경사항' 패널을 강조한다 |
