"""웹 정적 UI(index.html)에 레이아웃 조정 기능이 갖춰져 있는지 가볍게 검증한다.

서버를 띄우거나 브라우저를 실행하지 않고, index.html 텍스트만 읽어 필수 요소의
id/클래스와 localStorage 키가 존재하는지 확인한다. node 가 있으면 <script> 본문을
`node --check` 로 문법 검사하고, 없으면 skip 한다.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HTML_PATH = Path(__file__).resolve().parents[1] / "jokate" / "web_static" / "index.html"


@pytest.fixture(scope="module")
def html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


REQUIRED_IDS = [
    "vsplit",       # 왼쪽 열 / 상세 경계선
    "hsplit",       # 변경사항 / 타임라인 경계선
    "histSplit",    # 변경 애셋 표 / 버전 히스토리 경계선
    "detailMain",
    "histPane",
    "fullBtn",      # 상세 크게 보기 버튼
    "viewBtn",      # 보기 옵션 메뉴 버튼
    "viewMenu",
    "resetLayout",  # 레이아웃 초기화 버튼
    "optRowThumb",  # 행 썸네일 표시 체크박스
    "statsWrap",
    "statsMini",
    "baseRef",      # '마지막으로 올린 #N 이후' 기준 표시
    "hideAutoCb",   # 자동 저장 숨기기 토글
]


def test_revert_and_polling_present(html: str) -> None:
    assert "'/api/revert'" in html, "우클릭 되돌리기가 /api/revert 를 쓰지 않습니다"
    assert "jokate.hideAuto" in html, "자동 저장 숨기기 localStorage 키가 없습니다"
    assert "pollTick" in html and "document.hidden" in html, "5초 폴링/탭 숨김 처리가 없습니다"
    assert "s.uploaded" in html, "타임라인의 '올림' 표시가 없습니다"


@pytest.mark.parametrize("el_id", REQUIRED_IDS)
def test_required_ids_present(html: str, el_id: str) -> None:
    assert f'id="{el_id}"' in html, f"필수 요소 id={el_id} 가 index.html 에 없습니다"


def test_fold_toggle_buttons_present(html: str) -> None:
    for target in ["stats", "changes", "tlcard", "hist"]:
        assert f'data-fold="{target}"' in html, f"접기 토글(data-fold={target})이 없습니다"


def test_view_menu_options_present(html: str) -> None:
    for attr in ['data-density="compact"', 'data-density="normal"', 'data-density="roomy"',
                 'data-verthumb="96"', 'data-verthumb="156"', 'data-verthumb="224"']:
        assert attr in html, f"보기 옵션 {attr} 가 없습니다"


def test_localstorage_key_present(html: str) -> None:
    assert "jokate.layout.v1" in html


def test_splitter_hit_area_and_cursor_css(html: str) -> None:
    assert ".splitter" in html
    assert "col-resize" in html
    assert "row-resize" in html


def test_css_layout_variables_present(html: str) -> None:
    for var in ["--left-w", "--changes-h", "--hist-h", "--row-pad", "--thumb", "--ver-thumb"]:
        assert var in html, f"CSS 변수 {var} 가 없습니다"


def test_no_opacity_zero_start_animation(html: str) -> None:
    # keyframes 블록 안에서 from { opacity:0 ... } 형태의 시작이 없어야 한다
    import re
    for block in re.findall(r"@keyframes[^{]*\{(.*?)\n  \}", html, re.S):
        first = block.strip().split("}")[0]
        assert "opacity:0" not in first.replace(" ", "") or "from" not in first[:20], (
            "opacity 0 에서 시작하는 애니메이션은 금지됩니다"
        )


def test_script_syntax_with_node_if_available() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 가 설치되어 있지 않아 문법 검사를 건너뜁니다")
    html_text = HTML_PATH.read_text(encoding="utf-8")
    import re
    m = re.search(r"<script>(.*?)</script>", html_text, re.S)
    assert m, "인라인 <script> 를 찾지 못했습니다"
    tmp = Path(HTML_PATH.parent) / "_web_static_check_tmp.js"
    tmp.write_text(m.group(1), encoding="utf-8")
    try:
        r = subprocess.run([node, "--check", str(tmp)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    finally:
        tmp.unlink(missing_ok=True)
