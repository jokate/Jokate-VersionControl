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
```

## 구성

- `jokate/uasset.py` — 패키지 헤더 파서 (요약·이름·임포트·익스포트·썸네일). UE 4.11 ~ 5.7 검증
- `jokate/scan.py` — 등급 분류 + 애셋 레코드(클래스·부모·의존성·해시)
- `jokate/config.py` — 프로젝트 설정
- `jokate/__main__.py` — CLI

## 로드맵

1. ~~헤더 파서 + 스캐너~~
2. 스냅샷 저장소 (내용주소 + SQLite) + 타임라인 웹 UI
3. 롤백 (방금 저장 취소 → 애셋 히스토리 → 스냅샷 취소 → 시점 전체 복귀) + UE Python 브릿지
4. 리세이브 noise 필터, rename 추적, 참조 검산
