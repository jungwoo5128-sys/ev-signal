"""판정 규칙 테스트."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.judge import JudgeContext, judge, timing_advice  # noqa: E402

# 케이스 A: 예시 프로필
BASE = JudgeContext(
    bep_years=4.12,
    hold_years=7,
    home_charger=True,
    housing="아파트",
    long_trip="월3회이상",
    range_cold=369,
    annual_km=15000,
)


def test_case_a_green():
    grade, reasons = judge(BASE)
    assert grade == "GREEN"
    assert reasons == []


def test_case_b_no_charger_villa_red():
    grade, reasons = judge(replace(BASE, home_charger=False, housing="빌라·오피스텔"))
    assert grade == "RED"
    assert [s for s, _ in reasons].count("block") == 1


def test_case_c_bep_near_hold_yellow():
    grade, reasons = judge(replace(BASE, bep_years=6.0))
    assert grade == "YELLOW"
    assert reasons == [("warn", "회수 기간이 보유 기간에 근접 (BEP 6.0년)")]


def test_case_d_bep_inf_red():
    grade, reasons = judge(replace(BASE, bep_years=float("inf")))
    assert grade == "RED"
    assert ("block", "절감액이 없어 회수 불가") in reasons
    assert len(reasons) == 1  # 회수기간 사유는 하나만


def test_case_e_bep_zero_green():
    grade, reasons = judge(replace(BASE, bep_years=0.0))
    assert grade == "GREEN"
    assert not any("회수" in text for _, text in reasons)


def test_case_f_cold_range_yellow():
    grade, reasons = judge(replace(BASE, range_cold=330))
    assert grade == "YELLOW"
    assert reasons == [("warn", "겨울철 주행거리 330km — 장거리 시 충전 필요")]


def test_bep_over_hold_only_block_no_warn():
    grade, reasons = judge(replace(BASE, bep_years=8.0))
    assert grade == "RED"
    assert reasons == [("block", "보유 예정 7년 내 회수 불가 (BEP 8.0년)")]


def test_judge_is_deterministic():
    ctx = replace(BASE, home_charger=False, annual_km=5000, bep_years=6.0)
    assert all(judge(ctx) == judge(ctx) for _ in range(10))


@pytest.mark.parametrize("status, rate, expected", [
    ("접수중", 67, "ok"),
    ("접수중", 103, "over"),
    ("마감", 67, "closed"),
    ("접수중", 85, "urgent"),
    ("접수중", None, "unknown"),
    ("접수중", float("nan"), "unknown"),
    ("마감", None, "closed"),  # 상태가 결측 확인보다 우선
])
def test_timing_advice(status, rate, expected):
    level, message = timing_advice({"접수상태": status, "접수율": rate})
    assert level == expected
    assert message
