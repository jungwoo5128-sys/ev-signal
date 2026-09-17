"""계산 함수."""

import math

import pandas as pd

from config import (
    CHARGING_PRICE,
    CO2_ELECTRIC,
    CO2_GASOLINE,
    COMMUTE_DAYS_PER_WEEK,
    GASOLINE_PRICE,
    PINE_ABSORPTION,
    WEEKEND_KM_BY_LONG_TRIP,
    WEEKS_PER_YEAR,
)


def _require_positive(name: str, value) -> float:
    """None/NaN/0 이하 값이면 ValueError."""
    if value is None or pd.isna(value):
        raise ValueError(f"{name} 값이 없습니다.")
    value = float(value)
    if value <= 0:
        raise ValueError(f"{name} 값은 0보다 커야 합니다: {value}")
    return value


def format_km(value) -> str:
    """거리 표시용. 정수면 소수점 없이('40'), 소수면 필요한 자리까지('15.2'), 천 단위 쉼표."""
    value = float(value)
    if value.is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _round_half_up(value: float) -> int:
    # round()는 은행가 반올림(0.5 → 짝수)이라 사용자 기대와 다를 수 있다
    return math.floor(value + 0.5)


def calc_annual_km_from_commute(commute_km, long_trip) -> dict:
    """출퇴근 왕복 거리(소수 허용)와 장거리 주행 빈도로 연간 주행거리(km)를 환산한다.

    연간 = 출퇴근 왕복 × 주 5일 × 52주 + 주말·기타 주행(장거리 빈도별)
    annual_km은 정수(반올림)로 반환한다.
    """
    if long_trip not in WEEKEND_KM_BY_LONG_TRIP:
        raise ValueError(f"장거리 주행 빈도 값이 올바르지 않습니다: {long_trip}")
    commute_year = float(commute_km) * COMMUTE_DAYS_PER_WEEK * WEEKS_PER_YEAR
    weekend = float(WEEKEND_KM_BY_LONG_TRIP[long_trip])
    return {
        "commute_km_year": commute_year,
        "weekend_km_year": weekend,
        "annual_km": _round_half_up(commute_year + weekend),
    }


def calc_efficiency(battery_kwh, range_normal) -> float:
    """전비(km/kWh) = 상온 주행거리 ÷ 배터리 용량."""
    battery_kwh = _require_positive("battery_kwh", battery_kwh)
    range_normal = _require_positive("range_normal", range_normal)
    return range_normal / battery_kwh


def calc_fuel_saving(annual_km, current_efficiency, ev_efficiency) -> dict:
    """연간 유류비, 충전비, 절감액(원). current_efficiency는 비교 내연기관차 연비(km/L)."""
    annual_fuel_cost = (annual_km / current_efficiency) * GASOLINE_PRICE
    annual_charge_cost = (annual_km / ev_efficiency) * CHARGING_PRICE
    return {
        "annual_fuel_cost": float(annual_fuel_cost),
        "annual_charge_cost": float(annual_charge_cost),
        "saving": float(annual_fuel_cost - annual_charge_cost),
    }


def calc_subsidy(model_info: dict, has_scrap: bool) -> dict:
    """폐차 여부에 따른 적용 보조금과 내역(원)."""
    key = "subsidy_with_scrap" if has_scrap else "subsidy_total"

    def amount(name):
        value = model_info.get(name)
        return None if value is None or pd.isna(value) else float(value)

    return {
        "subsidy": amount(key),
        "subsidy_national": amount("subsidy_national"),
        "subsidy_local": amount("subsidy_local"),
        "scrap_national": amount("scrap_national") if has_scrap else 0.0,
        "scrap_local": amount("scrap_local") if has_scrap else 0.0,
        "has_scrap": has_scrap,
    }


def calc_co2(annual_km, current_efficiency, ev_efficiency) -> dict:
    """연간 CO2 배출량, 감축량, 소나무 환산."""
    gasoline_kg = (annual_km / current_efficiency) * CO2_GASOLINE
    electric_kg = (annual_km / ev_efficiency) * CO2_ELECTRIC
    reduction_kg = gasoline_kg - electric_kg
    return {
        "gasoline_kg": float(gasoline_kg),
        "electric_kg": float(electric_kg),
        "reduction_kg": float(reduction_kg),
        "reduction_ton": float(reduction_kg / 1000),
        "pine_trees": float(reduction_kg / PINE_ABSORPTION),
    }


def calc_price_gap(ev_price, ice_price) -> float:
    """전기차 − 내연기관차 가격 차이(원). 전기차가 더 싸면 0."""
    return float(max(ev_price - ice_price, 0))


def calc_bep(price_gap, subsidy=0, annual_saving=0) -> dict:
    """실부담(원)과 손익분기 연수. 회수 불가면 inf, 보조금이 차액보다 크면 0.0."""
    net_cost = float(price_gap - subsidy)
    if net_cost < 0:
        bep_years = 0.0
    elif annual_saving <= 0:
        bep_years = float("inf")
    else:
        bep_years = net_cost / annual_saving
    return {"net_cost": net_cost, "bep_years": float(bep_years)}
