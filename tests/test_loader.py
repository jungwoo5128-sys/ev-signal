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


def test_subsidy_rules_document_loaded():
    """규정 문서 내용은 사용자가 관리하므로 섹션 구성은 고정하지 않고, 챗봇이 쓰는 요소만 확인한다."""
    text = loader.load_subsidy_rules()
    assert text.startswith("# 2026년 전기차 보조금 공통 규정")
    assert "출처:" in text  # 챗봇이 출처명을 이 줄에서 가져온다
    assert "## 지자체별로 다른 사항" in text


def test_subsidy_rules_missing_file(tmp_path):
    assert loader.load_subsidy_rules(tmp_path / "없음.md") == ""


def test_subsidy_rules_national_surcharges_and_no_contradiction():
    text = loader.load_subsidy_rules()
    for fact in ("국고보조금의 20% 추가 지원", "2자녀 100만원", "3자녀 200만원", "4자녀 이상 300만원",
                 "비율 방식(청년·취약계층)을 먼저 산정하고, 정액 방식(다자녀)을 마지막에 더함",
                 "소상공인 확인 방법"):
        assert fact in text
    # 이제 자료에 있는 기준(다자녀 인원)은 '자료에 없음/지자체별'로 적지 않는다
    assert "다자녀 기준 자녀 인원" not in text
    # 청년 20% 가산은 국비 공통이지만 연령 기준은 자료에 없으므로 지자체별 확인 항목으로 명시
    local_section = text.split("## 지자체별로 다른 사항", 1)[1]
    assert "- 청년 연령 기준" in local_section



# --- 모델별 기본 가격 --------------------------------------------------------

def test_model_prices_match_excel_models(model_df):
    prices = loader.load_model_prices()
    assert prices["더 뉴 아이오닉5 2WD 롱레인지 19인치"]["base_price_manwon"] == 5290
    assert prices["EV3 롱레인지 2WD 17인치"]["base_price_manwon"] == 4415
    models = set(model_df["모델명"])
    for name, info in prices.items():
        assert name in models, name  # 엑셀 모델명과 정확히 일치
        assert isinstance(info["base_price_manwon"], int) and info["base_price_manwon"] > 0
        assert info["source"].startswith("https://")


def test_model_prices_missing_or_invalid(tmp_path):
    assert loader.load_model_prices(tmp_path / "없음.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert loader.load_model_prices(broken) == {}
    partial = tmp_path / "partial.json"
    partial.write_text(
        '{"A": {"base_price_manwon": 5000}, "B": {"base_price_manwon": 0}, '
        '"C": {"base_price_manwon": "5000"}, "D": {"base_price_manwon": true}, "E": 1}',
        encoding="utf-8",
    )
    assert list(loader.load_model_prices(partial)) == ["A"]
