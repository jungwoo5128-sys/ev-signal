"""계산 검산 테스트."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.calc import (  # noqa: E402
    calc_bep,
    calc_co2,
    calc_efficiency,
    calc_fuel_saving,
    calc_subsidy,
)

WON_TOL = 1000
TOL = 0.05

# 성남시 · 더 뉴 아이오닉5 2WD 롱레인지 19인치 · 폐차 O
BATTERY_KWH = 83.6
RANGE_NORMAL = 462
ANNUAL_KM = 15000
CURRENT_EFFICIENCY = 11.2
PRICE_GAP = 16_000_000
MODEL_INFO = {
    "battery_kwh": BATTERY_KWH,
    "range_normal": RANGE_NORMAL,
    "range_cold": 369,
    "subsidy_national": 5_640_000,
    "subsidy_local": 1_780_000,
    "scrap_national": 1_000_000,
    "scrap_local": 1_780_000,
    "subsidy_total": 7_420_000,
    "subsidy_with_scrap": 10_200_000,
    "maker": "현대자동차",
}


@pytest.fixture
def ev_efficiency():
    return calc_efficiency(BATTERY_KWH, RANGE_NORMAL)


def test_efficiency(ev_efficiency):
    assert ev_efficiency == pytest.approx(5.53, abs=TOL)


def test_fuel_saving(ev_efficiency):
    result = calc_fuel_saving(ANNUAL_KM, CURRENT_EFFICIENCY, ev_efficiency)
    assert result["annual_fuel_cost"] == pytest.approx(2_277_000, abs=WON_TOL)
    assert result["annual_charge_cost"] == pytest.approx(869_000, abs=WON_TOL)
    assert result["saving"] == pytest.approx(1_408_000, abs=WON_TOL)


def test_subsidy_with_scrap():
    result = calc_subsidy(MODEL_INFO, has_scrap=True)
    assert result["subsidy"] == pytest.approx(10_200_000, abs=WON_TOL)


def test_subsidy_without_scrap():
    result = calc_subsidy(MODEL_INFO, has_scrap=False)
    assert result["subsidy"] == pytest.approx(7_420_000, abs=WON_TOL)


def test_co2(ev_efficiency):
    result = calc_co2(ANNUAL_KM, CURRENT_EFFICIENCY, ev_efficiency)
    assert result["reduction_ton"] == pytest.approx(1.85, abs=TOL)
    # 기대값 280은 정수 반올림 값(실제 279.8)이라 그루 단위 ±0.5로 비교
    assert result["pine_trees"] == pytest.approx(280, abs=0.5)


def test_bep(ev_efficiency):
    saving = calc_fuel_saving(ANNUAL_KM, CURRENT_EFFICIENCY, ev_efficiency)["saving"]
    subsidy = calc_subsidy(MODEL_INFO, has_scrap=True)["subsidy"]
    result = calc_bep(PRICE_GAP, subsidy, saving)
    assert result["net_cost"] == pytest.approx(5_800_000, abs=WON_TOL)
    assert result["bep_years"] == pytest.approx(4.1, abs=TOL)


@pytest.mark.parametrize("battery, range_normal", [
    (None, RANGE_NORMAL),
    (float("nan"), RANGE_NORMAL),
    (BATTERY_KWH, None),
    (BATTERY_KWH, float("nan")),
])
def test_efficiency_missing_raises(battery, range_normal):
    with pytest.raises(ValueError):
        calc_efficiency(battery, range_normal)


def test_bep_no_saving_is_inf():
    result = calc_bep(PRICE_GAP, 10_200_000, 0)
    assert math.isinf(result["bep_years"])


def test_bep_subsidy_exceeds_gap_is_zero():
    result = calc_bep(PRICE_GAP, 17_000_000, 1_408_000)
    assert result["bep_years"] == 0.0
