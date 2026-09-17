"""배포 시 캐시 불일치 회귀 테스트.

Streamlit Cloud는 푸시 후 프로세스를 유지한 채 코드만 바꿔 실행하므로
@st.cache_data에 옛 DataFrame이 남을 수 있다. AppTest도 같은 프로세스에서
캐시를 공유하므로 이 상황을 재현할 수 있다.
"""

import sys
import warnings
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import loader  # noqa: E402

APP = str(ROOT / "app.py")

pytestmark = pytest.mark.skipif(
    not list(loader.DATA_DIR.glob("*.xlsx")), reason="data/에 엑셀 파일이 없음"
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    warnings.filterwarnings("ignore")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # LLM 호출 없이 폴백으로
    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture
def load_calls(monkeypatch):
    calls = []
    original = loader.load_data

    def spy(path=None):
        calls.append(tuple(loader.SUMMARY_COLUMNS))
        return original(path)

    monkeypatch.setattr(loader, "load_data", spy)
    return calls


def run_example():
    at = AppTest.from_file(APP, default_timeout=120).run()
    next(b for b in at.button if b.label == "예시 프로필로 채우기").click().run()
    return at


def test_cache_invalidated_when_loader_columns_change(monkeypatch, load_calls):
    # 1) 배포 전 코드: '비고'를 읽지 않던 loader로 캐시가 채워짐
    old_columns = [c for c in loader.SUMMARY_COLUMNS if c != "비고"]
    new_columns = list(loader.SUMMARY_COLUMNS)
    monkeypatch.setattr(loader, "SUMMARY_COLUMNS", old_columns)
    assert not run_example().exception
    assert len(load_calls) == 1

    # 2) 배포 후 코드: 컬럼 구성이 바뀌었으므로 캐시를 쓰지 않고 다시 읽어야 함
    monkeypatch.setattr(loader, "SUMMARY_COLUMNS", new_columns)
    at = run_example()
    assert not at.exception
    assert len(load_calls) == 2
    assert "비고" in load_calls[-1]


def test_cache_reused_when_nothing_changes(load_calls):
    run_example()
    run_example()
    assert len(load_calls) == 1


def test_app_survives_missing_notice_column(monkeypatch):
    """컬럼 구성은 같은데 데이터에 '비고'가 없어도 앱이 죽지 않고 공지 섹션만 숨긴다."""
    original = loader.load_data
    monkeypatch.setattr(
        loader, "load_data", lambda path=None: (original(path)[0].drop(columns=["비고"]), original(path)[1])
    )
    at = run_example()
    assert not at.exception
    assert not at.error
    markdown = " ".join(m.value for m in at.markdown if "<style>" not in m.value)
    assert "전환을 권장합니다" in markdown
    assert "공지사항 요약" not in markdown
