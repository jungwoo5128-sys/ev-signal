"""화면과 무관한 판정 흐름. app.py(Streamlit)에서 계산 조합·표시 포맷을 옮겨 왔다.

표시용 문자열도 여기서 만든다. LLM 숫자 검증이 이 포맷을 기준으로 하므로,
프론트엔드가 숫자를 따로 포맷하면 설명 문장과 카드 숫자가 어긋날 수 있다.
"""

import math
import threading
import time
from dataclasses import dataclass
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from config import COMMUTE_DAYS_PER_WEEK, WEEKS_PER_YEAR
from src import calc, judge, llm, loader

FALLBACK_RETRY_SECONDS = 60

CHAT_HISTORY_TURNS = 6  # 질문·답변 한 쌍을 한 턴으로 본다

LongTrip = Literal["거의없음", "월1~2회", "월3회이상"]
DistanceMode = Literal["annual", "commute"]  # 연간 주행거리 직접 입력 / 출퇴근 거리로 환산
WorkCharger = Literal["있음", "없음", "해당없음"]


class EvaluateInput(BaseModel):
    distance_mode: DistanceMode = "annual"
    annual_km: int | None = Field(default=None, gt=0)  # distance_mode="annual"일 때 사용
    commute_km: int | None = Field(default=None, ge=0)  # distance_mode="commute"일 때 사용
    long_trip: LongTrip
    region: str
    home_charger: bool
    work_charger: WorkCharger
    hold_years: Literal[3, 5, 7]
    model: str
    current_efficiency: float = Field(gt=0)
    ev_price_manwon: int = Field(ge=0)
    ice_price_manwon: int = Field(ge=0)
    has_scrap: bool

    @model_validator(mode="after")
    def _distance_for_mode(self):
        if self.distance_mode == "annual" and self.annual_km is None:
            raise ValueError("연간 주행거리를 입력하세요")
        if self.distance_mode == "commute" and self.commute_km is None:
            raise ValueError("출퇴근 왕복 거리를 입력하세요")
        return self


@dataclass
class Driving:
    """계산·판정에 쓰는 주행 정보."""
    annual_km: int
    commute_km: int | None  # 직접 입력 방식이면 모름(None) → 출퇴근 규칙 미적용
    source_note: str


def driving(inp: EvaluateInput) -> Driving:
    if inp.distance_mode == "commute":
        est = calc.calc_annual_km_from_commute(inp.commute_km, inp.long_trip)
        note = (
            f"출퇴근 {inp.commute_km:,}km × 주 {COMMUTE_DAYS_PER_WEEK}일 × {WEEKS_PER_YEAR}주"
            f" + 주말·기타 {est['weekend_km_year']:,.0f}km"
        )
        return Driving(round(est["annual_km"]), inp.commute_km, note)
    return Driving(inp.annual_km, None, "직접 입력")


# --- 표시 포맷 (app.py와 동일) -------------------------------------------

def won(value):
    return f"{value:,.0f}원"


def won_k(value):
    """천원 단위 반올림 (연간 비용 추정치)."""
    return won(round(value, -3))


def manwon(value):
    return f"{(value or 0) / 10000:,.0f}"


def format_bep(years):
    if math.isinf(years):
        return "회수 불가"
    if years == 0.0:
        return "즉시 회수"
    return f"{years:.1f}년"


def format_net_cost(value, ev_price, ice_price):
    if value > 0:
        return won(value)
    if ev_price <= ice_price:
        return "0원 (전기차가 더 저렴)"
    return "0원 (보조금으로 전액 충당)"


# --- 데이터 ----------------------------------------------------------------

@dataclass
class Store:
    summary_df: pd.DataFrame
    model_df: pd.DataFrame
    base_date: str


def load_store(path=None) -> Store:
    path = path or loader.find_latest_file()
    summary_df, model_df = loader.load_data(path)
    base_date = loader.FILE_PATTERN.search(path.name).group(1)
    return Store(summary_df, model_df, base_date)


def regions(store: Store) -> list[str]:
    return loader.get_regions(store.summary_df)


def complete_models(store: Store, region: str) -> list[str]:
    """배터리·상온·저온 주행거리가 모두 있는 모델만."""
    df = store.model_df
    rows = df[
        (df["지역구분"] == region)
        & df["battery_kwh"].notna()
        & df["range_normal"].notna()
        & df["range_cold"].notna()
    ]
    return rows["모델명"].drop_duplicates().tolist()


# --- 판정 ------------------------------------------------------------------

@dataclass
class Evaluation:
    grade: str
    reasons: list[tuple[str, str]]
    ctx: judge.JudgeContext
    calc_results: dict
    model_info: dict
    status: dict
    level: str
    advice: str
    driving: Driving


def _evaluate(store: Store, inp: EvaluateInput) -> Evaluation:
    """KeyError(지역·모델 없음), ValueError(제원 누락)는 호출부에서 처리."""
    if inp.region not in regions(store):
        raise KeyError(f"'{inp.region}' 지역이 없습니다.")
    model_info = loader.get_model_info(store.model_df, inp.region, inp.model)
    ev_eff = calc.calc_efficiency(model_info["battery_kwh"], model_info["range_normal"])
    drive = driving(inp)

    fuel = calc.calc_fuel_saving(drive.annual_km, inp.current_efficiency, ev_eff)
    subsidy = calc.calc_subsidy(model_info, inp.has_scrap)
    co2 = calc.calc_co2(drive.annual_km, inp.current_efficiency, ev_eff)
    ev_price = inp.ev_price_manwon * 10000  # 만원 → 원
    ice_price = inp.ice_price_manwon * 10000
    price_gap = calc.calc_price_gap(ev_price, ice_price)
    bep = calc.calc_bep(price_gap, subsidy["subsidy"] or 0, fuel["saving"])
    status = loader.get_region_status(store.summary_df, inp.region)

    ctx = judge.JudgeContext(
        bep_years=bep["bep_years"],
        hold_years=inp.hold_years,
        home_charger=inp.home_charger,
        work_charger=inp.work_charger == "있음",  # '해당없음'은 '없음'과 동일
        long_trip=inp.long_trip,
        range_cold=model_info["range_cold"],
        annual_km=drive.annual_km,
        commute_km=drive.commute_km,
    )
    grade, reasons = judge.judge(ctx)
    level, advice = judge.timing_advice(status)

    calc_results = {
        "fuel": fuel,
        "subsidy": subsidy,
        "co2": co2,
        "bep": bep,
        "inputs": {
            "current_efficiency": inp.current_efficiency,
            "ev_price": ev_price,
            "ice_price": ice_price,
            "price_gap": price_gap,
            "battery_kwh": model_info["battery_kwh"],
            "range_normal": model_info["range_normal"],
        },
    }
    return Evaluation(grade, reasons, ctx, calc_results, model_info, status, level, advice, drive)


def evaluate(store: Store, inp: EvaluateInput) -> dict:
    """등급·사유와 화면에 그대로 쓸 표시 문자열."""
    ev = _evaluate(store, inp)
    fuel = ev.calc_results["fuel"]
    subsidy = ev.calc_results["subsidy"]
    co2 = ev.calc_results["co2"]
    bep = ev.calc_results["bep"]
    prices = ev.calc_results["inputs"]
    subsidy_amount = subsidy["subsidy"] or 0

    breakdown = f"국비 {manwon(subsidy['subsidy_national'])}만 + 지방비 {manwon(subsidy['subsidy_local'])}만"
    if inp.has_scrap:
        scrap = (subsidy["scrap_national"] or 0) + (subsidy["scrap_local"] or 0)
        breakdown += f" + 전환 {manwon(scrap)}만원"

    status = ev.status
    if ev.level == "unknown":
        figures = "접수율 정보 없음 · 출고잔여 정보 없음"
    else:
        remain = status["출고잔여"]
        remain_text = f"{remain:,}대" if remain is not None else "정보 없음"
        figures = f"접수율 {status['접수율']}% · 출고잔여 {remain_text}"
    contact = " ".join(str(v) for v in (status["담당부서"], status["연락처"]) if v)

    info = ev.model_info
    return {
        "grade": ev.grade,
        "reasons": [{"severity": s, "text": t} for s, t in ev.reasons],
        "cards": {
            "fuel_saving": {
                "value": won_k(fuel["saving"]),
                "note": f"연 {ev.driving.annual_km:,}km · 연비 {inp.current_efficiency}km/L 기준",
            },
            "subsidy": {"value": won(subsidy_amount), "note": breakdown},
            "co2": {
                "value": f"{co2['reduction_ton']:.2f}톤",
                "note": f"소나무 {co2['pine_trees']:.0f}그루가 1년간 흡수하는 양",
            },
        },
        "status": {
            "level": ev.level,
            "advice": ev.advice,
            "figures": figures,
            "has_notice": bool(status["notice"]),
            "notice": status["notice"],
            "contact": contact or None,
        },
        "evidence": {
            "vehicle_cost": [
                {"label": "전기차 가격", "value": won(prices["ev_price"]), "note": "사용자 입력"},
                {"label": "보조금", "value": f"-{subsidy_amount:,.0f}원", "note": ""},
                {"label": "보조금 적용 후", "value": won(prices["ev_price"] - subsidy_amount), "note": ""},
                {"label": "비교 내연기관차 가격", "value": won(prices["ice_price"]), "note": "사용자 입력"},
                {
                    "label": "실제 추가 부담",
                    "value": format_net_cost(bep["net_cost"], prices["ev_price"], prices["ice_price"]),
                    "note": "",
                },
            ],
            "running_cost": [
                {"label": "연간 주행거리", "value": f"{ev.driving.annual_km:,}km", "note": ev.driving.source_note},
                {"label": "현재 차량 연간 유류비", "value": won_k(fuel["annual_fuel_cost"]), "note": ""},
                {"label": "전기차 연간 충전비", "value": won_k(fuel["annual_charge_cost"]), "note": ""},
                {"label": "연간 절감액", "value": won_k(fuel["saving"]), "note": ""},
            ],
            "result": [
                {"label": "회수 기간", "value": format_bep(bep["bep_years"]), "note": "실제 추가 부담 ÷ 연간 절감액"},
            ],
            "spec": (
                f"배터리 {info['battery_kwh']}kWh · "
                f"상온 {info['range_normal']}km / 저온 {info['range_cold']}km"
            ),
        },
        "driving": {"mode": inp.distance_mode, "annual_km": ev.driving.annual_km},
        "base_date": store.base_date,
    }


# --- LLM (서버 캐시) ----------------------------------------------------------
# 실패 결과도 잠시 캐싱해, 네트워크가 끊겼을 때 요청마다 타임아웃을 기다리지 않게 한다.

class _TimedCache:
    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key, is_failure):
        with self._lock:
            cached = self._data.get(key)
        if cached is None:
            return None
        value, created = cached
        if is_failure(value) and time.time() - created >= FALLBACK_RETRY_SECONDS:
            return None
        return cached

    def set(self, key, value):
        with self._lock:
            self._data[key] = (value, time.time())


_explain_cache = _TimedCache()
_notice_cache = _TimedCache()


def explain(store: Store, inp: EvaluateInput) -> dict:
    """판정을 서버에서 다시 계산해 설명을 만든다 (클라이언트가 보낸 등급을 믿지 않음)."""
    ev = _evaluate(store, inp)
    key = repr((ev.grade, ev.reasons, ev.ctx, ev.calc_results))
    cached = _explain_cache.get(key, lambda e: e["fallback"])
    if cached:
        return cached[0]
    explanation = llm.generate_reason(ev.grade, ev.reasons, ev.ctx, ev.calc_results)
    _explain_cache.set(key, explanation)
    return explanation


def notice_summary(store: Store, region: str, has_scrap: bool, model: str):
    """요약 항목 리스트. 공지가 없거나 실패하면 None (섹션을 숨긴다)."""
    notice = loader.get_region_status(store.summary_df, region)["notice"]
    if not notice:
        return None
    key = repr((region, notice, has_scrap, model))
    cached = _notice_cache.get(key, lambda items: items is None)
    if cached:
        return cached[0]
    items = llm.summarize_notice(notice, has_scrap, model)
    _notice_cache.set(key, items)
    return items


# --- 보조금 자격 문의 챗봇 -----------------------------------------------------
# 판정(judge)과 무관한 정보 제공. 답변 생성·안전장치는 llm.answer_eligibility()에 있다.

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


NO_REGION_LABEL = "지자체 미선택"


class ChatInput(BaseModel):
    region: str | None = None  # 없으면 공통 규정만으로 답한다
    question: str = Field(min_length=1, max_length=500)
    history: list[ChatMessage] = []


def region_contact(store: Store, region: str) -> str | None:
    """챗봇 고지에 쓰는 담당부서·연락처."""
    if region not in regions(store):
        raise KeyError(f"'{region}' 지역이 없습니다.")
    status = loader.get_region_status(store.summary_df, region)
    return " ".join(str(v) for v in (status["담당부서"], status["연락처"]) if v) or None


def chat(store: Store, inp: ChatInput) -> dict:
    """근거 문서(공통 규정 + 지자체 공지)만으로 답한다. 실패 시에도 안내 문구를 answer로 돌려준다.

    지자체를 고르지 않았으면 공지 없이 공통 규정만 근거로 쓴다.
    """
    if inp.region:
        if inp.region not in regions(store):
            raise KeyError(f"'{inp.region}' 지역이 없습니다.")
        region = inp.region
        notice = loader.get_region_status(store.summary_df, region)["notice"]
    else:
        region, notice = NO_REGION_LABEL, None
    history = [m.model_dump() for m in inp.history][-CHAT_HISTORY_TURNS * 2:]
    result = llm.answer_eligibility(inp.question, history, region, notice, loader.load_subsidy_rules())
    return {"answer": result["answer"]}
