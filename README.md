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
python -m jokate snap    <project> [-m "메시지"]  # authored 스냅샷. -m 있으면 label, 없으면 auto. 변경 없으면 생략(--force)
python -m jokate log     <project>               # 스냅샷 목록
python -m jokate show    <project> <id>          # 직전 스냅샷 대비 추가(A)/수정(M)/이동(R)/삭제(D) + 클래스별 집계
python -m jokate restore <project> <id> [--asset rel ...] [--apply]   # 롤백. 기본 드라이런, --apply 로 적용
python -m jokate watch   <project> [--interval 2] [--debounce 5]      # 저장 감지 자동 스냅샷 데몬 (Ctrl+C 종료)
```

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
- `UnrealEditor.exe` 가 실행 중이면 `--apply` 를 거부한다 (에디터를 닫고 다시 실행)
- 롤백도 되돌릴 수 있다: `restore <project> <안전 스냅샷 id> --apply`

## 스냅샷 저장소

- `.jokate/store/objects/<sha[:2]>/<sha>` — 원본 그대로(압축 없음), 내용주소(blake2b-256). 같은 내용은 한 번만 저장
- `.jokate/index.sqlite` — `snapshots(id, parent, kind, message, ts)`, `tree(snapshot_id, rel, sha, size, cls, deps)`
- 변경 판단은 mtime 이 아니라 sha 비교. 이동은 "sha 동일 + 경로 변경"으로 잡는다
- vendor 등급은 스냅샷에 포함하지 않는다

## 구성

- `jokate/uasset.py` — 패키지 헤더 파서 (요약·이름·임포트·익스포트·썸네일). UE 4.11 ~ 5.7 검증
- `jokate/scan.py` — 등급 분류 + 애셋 레코드(클래스·부모·의존성·해시)
- `jokate/store.py` — 스냅샷 저장소 (내용주소 객체 + SQLite 인덱스, 트리 diff)
- `jokate/watch.py` — 저장 감지 자동 스냅샷 데몬 (폴링 + debounce, `poll_once` 순수 함수)
- `jokate/config.py` — 프로젝트 설정
- `jokate/__main__.py` — CLI

## 로드맵

1. ~~헤더 파서 + 스캐너~~
2. 스냅샷 저장소 (내용주소 + SQLite) + 타임라인 웹 UI
3. ~~롤백 (시점 전체 복귀 / 애셋 단위, 드라이런 + 안전 스냅샷, 참조 검산)~~ → 애셋 히스토리, UE Python 브릿지
4. ~~저장 감지 자동 스냅샷 데몬 (watch)~~ → 리세이브 noise 필터, rename 추적
