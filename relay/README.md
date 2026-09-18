# 릴레이로 Jokate 개발하기

이 저장소의 구현·수정은 [Agent 카태 릴레이](https://github.com/jokate/Jokate-Agent)로 돌린다. 사람은 **스펙(프롬프트) → 실행 → 검증 → 커밋**만 한다.

## 한 사이클

```
1. relay\run.bat 17-layout          프롬프트를 릴레이로 보냄 (기본: quick-fable 릴레이 + Opus, 단계 예산 $2)
2. http://127.0.0.1:8020            대시보드에서 진행 확인 (저장소 jokate)
3. relay\verify.bat                 pytest · 브릿지 문법 · MNYS 전체 파싱 · 바뀐 파일
4. 직접 눌러 보기                     start.bat → 웹 UI / 에디터 (아래 '검증 요령')
5. git add -A && git commit         단계 하나 = 커밋 하나
```

릴레이는 **한 번에 하나만**. 같은 저장소에 두 개를 동시에 돌리면 결과가 서로 덮어쓴다. `run.bat` 은 커밋 안 된 변경이 있으면 실행을 거부한다.

## 대시보드에서 돌리기

```
1. relay-agent\start.bat                  대시보드 http://127.0.0.1:8020 (이미 떠 있으면 브라우저만 열림)
2. relay\copy.bat 17-layout               프롬프트를 클립보드에 복사
3. 대시보드 새 실행:  저장소 jokate · 릴레이 quick-fable · build 단계 모델 Opus · 목표에 Ctrl+V → 실행
4. 끝나면 relay\verify.bat → 직접 눌러 보기 → git commit
```

- 실행 전에 저장소가 깨끗한지 확인(`git status`). 커밋 안 된 변경이 있으면 릴레이 결과와 섞인다.
- 예산 도달·중단은 대시보드의 **승인 / 재개 / 취소 / 변경 적용·버리기·되돌리기** 버튼으로 처리한다(CLI 의 approve/resume/discard/rollback 과 같다).
- 실행마다 진행·이벤트·토큰·패치·인계서(HANDOFF) 화면이 있다. 릴레이가 남긴 `☐ 확인:` 항목이 사람이 눌러 볼 목록이다.

## CLI 로 돌리기 (옵션)

```
relay\run.bat 19a-baseline-store -Model fable          모델 바꾸기 (opus | fable | sonnet)
relay\run.bat 18b-editor-only-diff -Relay quick        작은 수정은 quick (Sonnet 기본, 예산 $0.8)
relay\run.bat C:\path\my-prompt.txt                    임의 파일
```

대시보드에서 직접 돌릴 때: 저장소 `jokate`, 릴레이 `quick-fable`, 프롬프트 파일 내용을 그대로 붙여 넣는다.

## 멈췄을 때

| 상황 | 명령 (relay-agent 폴더에서) |
|---|---|
| 예산 도달로 승인 대기 | `uv run relay approve <run id>` — 한도를 2배로 올려 이어감. 이미 한 작업은 유지 |
| `Session ID ... is already in use` 등으로 build 중단 | `uv run relay resume <run id>` — 비용 0 으로 재개 |
| 결과가 마음에 안 듦 | `uv run relay discard <run id>` (적용 전) / `uv run relay rollback <run id>` (적용 후) |
| 상태·로그·diff | `uv run relay status <id>` · `uv run relay log <id>` · `uv run relay patch <id>` |

## 프롬프트 쓰는 규칙 (겪은 사고들)

- **경로는 슬래시로만** (`C:/Users/...`). 백슬래시는 릴레이의 bash 에서 사라져 저장소 안에 `Userskkkk4017ProjectsMNYS/` 같은 폴더가 생긴다. `verify.bat` 이 검사한다.
- **프롬프트는 파일로.** 명령줄에 직접 쓰면 큰따옴표가 인자 파싱을 깬다. 프롬프트 안에 큰따옴표를 쓰지 않는다.
- **한 단계가 $2 에 들어가게 쪼갠다.** 저장소+CLI / 웹 UI / 에디터 브릿지를 한 프롬프트에 다 넣으면 예산에 걸린다(18단계가 그랬다). 기준: 파일 6개·+500줄 안팎.
- 끝에 항상: 검증 명령(`uv run --no-project --with pytest pytest -q`, 브릿지를 건드리면 `python -m py_compile jokate/ue/jokate_bridge.py`), **MNYS 에서 jokate 명령 실행 금지**, 실제 에디터 실행 금지, `편집은 Edit 도구로 파일별로, 일괄 치환 스크립트 금지`(일괄 치환 스크립트가 실패해 예산만 태운 적이 있다).
- UI 작업에는: `기존 디자인 토큰 재사용`, `opacity 0 에서 시작하는 CSS 애니메이션 금지`(미리보기 환경에서 멈춰 요소가 안 보인다), `기존 기능 유지` 와 그 기능 목록.
- 에디터 API 는 **추측하지 말고 확인한 사실을 적는다.** UE 5.7.4 에서 확인된 것: `AssetTools.diff_assets` + `RevisionInfo`, `DataTableFunctionLibrary.export_data_table_to_json_string` / `get_data_table_column_names` / `get_data_table_row_names` / `get_data_table_column_as_string`, `AssetExportTask` + `ObjectExporterT3D` + `Exporter.run_asset_export_task`(커스텀 DataAsset 도 T3D 텍스트로 나옴), `EditorDialog`, `ToolMenus`, `EditorLoadingAndSavingUtils.reload_packages`.

## 검증 요령

- **MNYS 를 직접 건드리는 시험은 피한다.** 실제 .uasset 몇 개를 복사한 임시 프로젝트(`Content/` + `.jokate/config.toml`, `[web] port = 8767`)를 만들어 `python -m jokate daemon <임시>` 로 띄워 시험한다. 8765 는 평소 쓰는 MNYS 데몬 몫.
- 파일의 끝쪽 바이트 하나를 뒤집으면 '실제 수정', 오프셋 24 부터 20바이트(SavedHash)만 바꾸면 '리세이브만' 을 흉내 낼 수 있다.
- 코드가 바뀌면 떠 있는 데몬은 낡은 것이다. 웹 상단의 '재시작 필요' 배너 → **지금 재시작**, 또는 `stop.bat` → `start.bat`.
- 에디터 쪽 스크립트가 바뀌면 `bridge-install.bat` 을 다시 실행하고 에디터를 재시작(또는 Python 콘솔에서 `import importlib, jokate_bridge; importlib.reload(jokate_bridge)`).
- 릴레이가 남기는 `☐ 확인:` 항목이 곧 사람이 눌러 봐야 할 목록이다.

## 대기 중인 프롬프트 (`prompts/`)

| 순서 | 파일 | 내용 | 의존 |
|---|---|---|---|
| 1 | `17-layout.txt` | 웹 UI 레이아웃 조정: 경계선 드래그 3개, 패널 접기, 상세 크게 보기, 보기 옵션, localStorage 저장 | — |
| 2 | `18b-editor-only-diff.txt` | 에디터가 켜져 있으면 diff 실패 시에도 두 번째 에디터를 띄우지 않음, 꺼져 있을 때만 확인 후 새 프로세스 | 18 |
| 3 | `19a-baseline-store.txt` | '올린 것(baseline)'과 '자동 기록'을 분리 — 자동 스냅샷이 찍혀도 변경사항 패널이 비지 않게. 저장소·CLI | — |
| 4 | `19b-baseline-ui.txt` | 위 모델을 웹·에디터 메뉴에 반영 + 타임라인 자동 갱신 | 19a |
| 5 | `20a-deps-diff.txt` | 수정된 애셋의 참조 추가/제거 표시 (에디터 불필요) | — |
| 6 | `20b-text-meta-dataasset.txt` | DataAsset 등 속성 값 diff (T3D 텍스트 사이드카) | 14a |

19a 는 저장소 모델을 바꾸는 큰 작업이다. 예산에 걸리면 승인하거나, 프롬프트의 (5) revert_to_baseline 과 (6) squash 병합을 떼어 19a-2 로 나눈다.
