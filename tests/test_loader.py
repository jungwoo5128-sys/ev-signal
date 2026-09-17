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
