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
    work_charger=False,
    long_trip="월3회이상",
    range_cold=369,
    annual_km=15000,
    commute_km=40,
)


def test_case_a_green():
    grade, reasons = judge(BASE)
    assert grade == "GREEN"
    assert reasons == []


def test_case_b_no_charger_red():
    grade, reasons = judge(replace(BASE, home_charger=False, work_charger=False))
    assert grade == "RED"
    assert [s for s, _ in reasons].count("block") == 1


@pytest.mark.parametrize("home, work, grade, reasons", [
    # 주거지 O → 근무지는 보지 않음
    (True, False, "GREEN", []),
    (True, True, "GREEN", []),
    # 주거지 X / 근무지 O
    (False, True, "YELLOW", [("warn", "주거지 충전 불가 — 근무지 충전에 의존")]),
    # 주거지 X / 근무지 X → block
    (False, False, "RED", [("block", "주거지·근무지 모두 충전 불가 — 공용 충전에 전적으로 의존")]),
])
def test_charger_combinations(home, work, grade, reasons):
    ctx = replace(BASE, home_charger=home, work_charger=work)
    assert judge(ctx) == (grade, reasons)


@pytest.mark.parametrize("home, work, commute, reasons", [
    # 주거지 X / 근무지 O / 출퇴근 80km → 근무지 의존 + 출퇴근 부담
    (False, True, 80, [
        ("warn", "주거지 충전 불가 — 근무지 충전에 의존"),
        ("warn", "출퇴근 왕복 80km + 주거지 충전 불가 — 공용 충전 의존도 높음"),
    ]),
    # 주거지 O / 출퇴근 80km → 출퇴근 규칙 미적용
    (True, False, 80, []),
    # 경계값: 60km부터 적용, 59km는 미적용
    (False, True, 60, [
        ("warn", "주거지 충전 불가 — 근무지 충전에 의존"),
        ("warn", "출퇴근 왕복 60km + 주거지 충전 불가 — 공용 충전 의존도 높음"),
    ]),
    (False, True, 59, [("warn", "주거지 충전 불가 — 근무지 충전에 의존")]),
])
def test_commute_rule(home, work, commute, reasons):
    grade, result = judge(replace(BASE, home_charger=home, work_charger=work, commute_km=commute))
    assert result == reasons
    assert grade == ("YELLOW" if reasons else "GREEN")


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
