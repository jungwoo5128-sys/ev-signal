"""엑셀 로더 테스트 (data/의 실제 엑셀 사용)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import loader  # noqa: E402

pytestmark = pytest.mark.skipif(
    not list(loader.DATA_DIR.glob("*.xlsx")), reason="data/에 엑셀 파일이 없음"
)


@pytest.fixture(scope="module")
def summary_df():
    return loader.load_data()[0]


def test_notice_present_for_155_regions(summary_df):
    statuses = [loader.get_region_status(summary_df, r) for r in loader.get_regions(summary_df)]
    assert sum(s["notice"] is not None for s in statuses) == 155


def test_notice_text_and_missing(summary_df):
    assert "성남시" in loader.get_region_status(summary_df, "성남시")["notice"]
    assert loader.get_region_status(summary_df, "의령군")["notice"] is None


# --- 컬럼 누락 방어 (데이터 파일·캐시 구성 불일치) -------------------------

@pytest.fixture(scope="module")
def model_df():
    return loader.load_data()[1]


def test_region_status_missing_columns_returns_none(summary_df):
    stale = summary_df.drop(columns=["비고", "접수율(%)", "담당부서"])
    status = loader.get_region_status(stale, "성남시")
    assert status["notice"] is None
    assert status["접수율"] is None
    assert status["담당부서"] is None
    assert status["출고잔여"] == 1713  # 남은 컬럼은 그대로


def test_region_status_unchanged_on_full_data(summary_df):
    status = loader.get_region_status(summary_df, "성남시")
    assert (status["접수율"], status["출고잔여"], status["접수상태"]) == (67, 1713, "접수중")
    assert status["notice"]


def test_model_info_missing_column_returns_none(model_df):
    model = "더 뉴 아이오닉5 2WD 롱레인지 19인치"
    info = loader.get_model_info(model_df.drop(columns=["제조사", "scrap_local"]), "성남시", model)
    assert info["maker"] is None and info["scrap_local"] is None
    assert info["subsidy_with_scrap"] == 10_200_000


def test_schema_signature_changes_with_columns(monkeypatch):
    before = loader.schema_signature()
    monkeypatch.setattr(loader, "SUMMARY_COLUMNS", [c for c in loader.SUMMARY_COLUMNS if c != "비고"])
    assert loader.schema_signature() != before


def test_subsidy_rules_document_sections():
    text = loader.load_subsidy_rules()
    for heading in ("# 2026년 전기차 보조금 공통 규정", "## 우선순위 대상", "## 국비 가산",
                    "## 전환지원금", "## 지자체별로 다른 사항"):
        assert heading in text


def test_subsidy_rules_missing_file(tmp_path):
    assert loader.load_subsidy_rules(tmp_path / "없음.md") == ""
