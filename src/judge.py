"""판정 규칙."""

import math
from dataclasses import dataclass

BLOCK = "block"
WARN = "warn"


@dataclass
class JudgeContext:
    bep_years: float  # inf 또는 0.0 가능
    hold_years: int  # 3 | 5 | 7
    home_charger: bool  # 주거지 충전기 있음
    work_charger: bool  # 근무지 충전기 있음 ('해당없음'은 False)
    long_trip: str  # '거의없음' | '월1~2회' | '월3회이상'
    range_cold: int
    annual_km: int
    commute_km: int | None  # 출퇴근 왕복 거리. 연간 주행거리를 직접 입력한 경우 None


def _bep_reason(ctx: JudgeContext):
    """회수 기간 사유. 위에서부터 첫 번째로 걸린 하나만 반환."""
    bep = ctx.bep_years
    if math.isinf(bep):
        return (BLOCK, "절감액이 없어 회수 불가")
    if bep == 0.0:
        return None
    if bep > ctx.hold_years:
        return (BLOCK, f"보유 예정 {ctx.hold_years}년 내 회수 불가 (BEP {bep:.1f}년)")
    if bep > ctx.hold_years * 0.7:
        return (WARN, f"회수 기간이 보유 기간에 근접 (BEP {bep:.1f}년)")
    return None


def _charger_reason(ctx: JudgeContext):
    """주거지 충전이 되면 근무지는 보지 않는다."""
    if ctx.home_charger:
        return None
    if ctx.work_charger:
        return (WARN, "주거지 충전 불가 — 근무지 충전에 의존")
    return (BLOCK, "주거지·근무지 모두 충전 불가 — 공용 충전에 전적으로 의존")


def _long_trip_reason(ctx: JudgeContext):
    if ctx.long_trip == "월3회이상" and ctx.range_cold < 350:
        return (WARN, f"겨울철 주행거리 {ctx.range_cold}km — 장거리 시 충전 필요")
    return None


def _annual_km_reason(ctx: JudgeContext):
    if ctx.annual_km < 8000:
        return (WARN, "연간 주행거리가 적어 절감 효과 제한적")
    return None


def _commute_reason(ctx: JudgeContext):
    """주거지 충전이 가능하면 밤새 충전되므로 출퇴근 거리는 보지 않는다.

    출퇴근 거리를 모르는 경우(연간 주행거리 직접 입력, commute_km=None)는 평가하지 않는다.
    """
    if ctx.commute_km is None:
        return None
    if not ctx.home_charger and ctx.commute_km >= 60:
        return (WARN, f"출퇴근 왕복 {ctx.commute_km}km + 주거지 충전 불가 — 공용 충전 의존도 높음")
    return None


def judge(ctx: JudgeContext) -> tuple[str, list[tuple[str, str]]]:
    """등급('GREEN'|'YELLOW'|'RED')과 (심각도, 설명) 사유 리스트."""
    rules = (
        _bep_reason,
        _charger_reason,
        _long_trip_reason,
        _annual_km_reason,
        _commute_reason,
    )
    reasons = [r for rule in rules if (r := rule(ctx)) is not None]

    severities = {severity for severity, _ in reasons}
    if BLOCK in severities:
        grade = "RED"
    elif WARN in severities:
        grade = "YELLOW"
    else:
        grade = "GREEN"
    return grade, reasons


def timing_advice(region_status: dict) -> tuple[str, str]:
    """결정 시점 권고. judge() 등급과 독립적으로 항상 반환."""
    status = region_status.get("접수상태")
    rate = region_status.get("접수율")

    if status == "마감":
        return ("closed", "현재 접수 마감. 다음 공고 대기 필요")
    if status == "접수예정":
        return ("closed", "아직 접수 시작 전. 공고 일정 확인 필요")
    if rate is None or (isinstance(rate, float) and math.isnan(rate)):
        return ("unknown", "현재 접수 현황을 확인할 수 없습니다. 관할 지자체에 문의하세요")
    if rate >= 100:
        return ("over", "이미 공고 대수 초과. 신청해도 대기 가능성")
    if rate >= 80:
        return ("urgent", "소진 임박. 결정을 서두르는 것이 유리")
    return ("ok", "현재 여유 있음")
