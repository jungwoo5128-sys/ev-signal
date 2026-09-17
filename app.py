"""Streamlit 진입점."""

import html
import math
import time

import streamlit as st

from src import calc, judge, llm, loader

st.set_page_config(page_title="전기차 신호등", layout="wide")

# 입력 흐름 UI 색상. 판정 램프(초록/노랑/빨강)와 섞지 않는다.
# 값은 임시 — 디자인 팔레트가 정해지면 여기만 바꾸면 된다.
st.markdown(
    """<style>
    :root {
        --accent: #d0217c;        /* 마젠타: 현재 단계, 주요 버튼 */
        --accent-hover: #a8185f;
        --line: #17a2b8;          /* 시안: 완료 단계, 연결선 */
        --muted: #c5c9d0;         /* 이후 단계 */
        --muted-text: #6b7280;
    }
    .st-key-nav_next button, .st-key-nav_submit button {
        background: var(--accent); border: 1px solid var(--accent); color: #fff;
    }
    .st-key-nav_next button:hover, .st-key-nav_submit button:hover {
        background: var(--accent-hover); border-color: var(--accent-hover); color: #fff;
    }
    .st-key-nav_next button:disabled, .st-key-nav_submit button:disabled {
        background: var(--muted); border-color: var(--muted); color: #fff; opacity: 0.7;
    }
    .st-key-nav_prev button {
        background: transparent; border: 1px solid var(--accent); color: var(--accent);
    }
    .st-key-nav_prev button:hover {
        background: transparent; border-color: var(--accent-hover); color: var(--accent-hover);
    }
    </style>""",
    unsafe_allow_html=True,
)

GRADE_COLORS = {"GREEN": "#2e9e5b", "YELLOW": "#e0a92b", "RED": "#d14343"}
GRADE_LABELS = {"GREEN": "추천", "YELLOW": "조건부", "RED": "비추천"}
GRADE_MESSAGES = {
    "GREEN": "전환을 권장합니다",
    "YELLOW": "조건을 확인한 뒤 결정하세요",
    "RED": "지금은 권장하지 않습니다",
}
OFF_COLOR = "#e0e0e0"

# (배경, 테두리)
TIMING_COLORS = {
    "ok": ("#e8f5ec", "#2e9e5b"),
    "urgent": ("#fdf4e0", "#e0a92b"),
    "over": ("#fbe9e9", "#d14343"),
    "closed": ("#f0f0f0", "#9e9e9e"),
    "unknown": ("#f0f0f0", "#9e9e9e"),
}

LONG_TRIP_OPTIONS = ["거의없음", "월1~2회", "월3회이상"]
HOUSING_OPTIONS = ["아파트", "단독", "빌라·오피스텔"]
WORK_CHARGER_OPTIONS = ["있음", "없음", "해당없음"]
HOLD_OPTIONS = {3: "3년", 5: "5년", 7: "7년 이상"}

DEFAULTS = {
    "annual_km": 15000,
    "commute_km": 40,
    "long_trip": "거의없음",
    "region": None,
    "housing": "아파트",
    "home_charger": True,
    "work_charger": "없음",
    "hold_years": 7,
    "model": None,
    "current_efficiency": 11.2,
    "ev_price_manwon": 5200,
    "ice_price_manwon": 3600,
    "has_scrap": True,
}

EXAMPLE_PROFILE = {
    "annual_km": 15000,
    "commute_km": 40,
    "long_trip": "월3회이상",
    "region": "성남시",
    "housing": "아파트",
    "home_charger": True,
    "work_charger": "없음",
    "hold_years": 7,
    "model": "더 뉴 아이오닉5 2WD 롱레인지 19인치",
    "current_efficiency": 11.2,
    "ev_price_manwon": 5200,
    "ice_price_manwon": 3600,
    "has_scrap": True,
}


@st.cache_data(show_spinner=False)
def load():
    path = loader.find_latest_file()
    summary_df, model_df = loader.load_data(path)
    base_date = loader.FILE_PATTERN.search(path.name).group(1)
    return summary_df, model_df, base_date


def complete_models(model_df, region):
    """배터리·상온 주행거리가 모두 있는 모델만."""
    rows = model_df[
        (model_df["지역구분"] == region)
        & model_df["battery_kwh"].notna()
        & model_df["range_normal"].notna()
        & model_df["range_cold"].notna()
    ]
    return rows["모델명"].drop_duplicates().tolist()


# --- 상태 ---------------------------------------------------------------
# 입력값은 위젯 키(w_*)와 별도로 보관해, 결과 화면에서 위젯이 사라져도 유지된다.

ss = st.session_state
ss.setdefault("step", "input")
ss.setdefault("input_step", 1)
for name, value in DEFAULTS.items():
    ss.setdefault(name, value)


def bind(name):
    widget_key = f"w_{name}"
    if widget_key not in ss:
        ss[widget_key] = ss[name]
    return widget_key


def reset_widgets():
    for name in DEFAULTS:
        ss.pop(f"w_{name}", None)
    ss.pop("w_result_region", None)


def fill_example():
    """모든 값을 채우고 단계를 건너뛰어 바로 결과 화면으로 이동 (시연용).

    콜백은 스크립트 실행 전에 돌기 때문에 위젯 key에 직접 할당해도 안전하다.
    """
    for name, value in EXAMPLE_PROFILE.items():
        ss[name] = value
        ss[f"w_{name}"] = value
    ss.input_step = 1
    ss.step = "result"
    ss.pop("w_result_region", None)


def go_to_step(n):
    ss.input_step = n


def submit():
    ss.step = "result"
    ss.pop("w_result_region", None)


def back_to_input():
    """결과 → STEP 1. 입력값(ss[name])은 유지되고 위젯은 그 값으로 다시 그려진다."""
    ss.step = "input"
    ss.input_step = 1
    reset_widgets()


# 로드가 끝나기 전에는 폼·버튼을 그리지 않는다 (준비 전 클릭이 무시되는 문제 방지)
loading = st.empty()
with loading.container(), st.spinner("데이터를 불러오는 중..."):
    summary_df, model_df, base_date = load()
loading.empty()


# --- 입력 화면 ----------------------------------------------------------

STEPS = ["주행", "거주", "차량·보유"]


def render_step_indicator(current):
    parts = []
    for i, label in enumerate(STEPS, start=1):
        if i == current:
            bg, fg, weight = "var(--accent)", "#fff", 700
        elif i < current:
            bg, fg, weight = "var(--line)", "#fff", 400
        else:
            bg, fg, weight = "var(--muted)", "var(--muted-text)", 400
        parts.append(
            f"""<div style="display:flex;flex-direction:column;align-items:center;min-width:64px">
                  <div style="width:36px;height:36px;border-radius:50%;background:{bg};color:{fg};
                              display:flex;align-items:center;justify-content:center;
                              font-weight:700">{i}</div>
                  <div style="margin-top:4px;font-size:13px;font-weight:{weight}">{label}</div>
                </div>"""
        )
        if i < len(STEPS):
            line = "var(--line)" if i < current else "var(--muted)"
            parts.append(
                f'<div style="flex:0 0 48px;height:3px;background:{line};margin-top:17px"></div>'
            )
    st.markdown(
        f'<div style="display:flex;align-items:flex-start;margin:8px 0 16px">{"".join(parts)}</div>',
        unsafe_allow_html=True,
    )


def step_ready(n):
    """각 단계의 필수값."""
    if n == 1:
        return ss.annual_km > 0
    if n == 2:
        return bool(ss.region)
    return bool(ss.region and ss.model and ss.current_efficiency > 0)


def render_step_driving():
    ss.annual_km = st.number_input(
        "연간 주행거리 (km)", min_value=0, step=1000, key=bind("annual_km")
    )
    ss.commute_km = st.number_input(
        "출퇴근 왕복 거리 (km)", min_value=0, step=1, key=bind("commute_km")
    )
    ss.long_trip = st.selectbox(
        "장거리 주행 빈도", LONG_TRIP_OPTIONS, key=bind("long_trip")
    )


def render_step_residence():
    ss.region = st.selectbox(
        "거주 지자체",
        loader.get_regions(summary_df),
        index=None,
        placeholder="지자체를 선택하세요",
        key=bind("region"),
    )
    ss.housing = st.selectbox("주거 형태", HOUSING_OPTIONS, key=bind("housing"))
    ss.home_charger = st.radio(
        "주거지 충전기",
        [True, False],
        format_func=lambda v: "있음" if v else "없음",
        horizontal=True,
        key=bind("home_charger"),
    )
    ss.work_charger = st.radio(
        "근무지 충전기",
        WORK_CHARGER_OPTIONS,
        horizontal=True,
        help="재택근무·무직 등은 '해당없음'을 선택하세요. 판정에서는 '없음'과 같습니다.",
        key=bind("work_charger"),
    )


def render_step_vehicle():
    ss.hold_years = st.selectbox(
        "예상 보유 기간",
        list(HOLD_OPTIONS),
        format_func=HOLD_OPTIONS.get,
        key=bind("hold_years"),
    )

    # 모델 목록은 STEP 2에서 고른 지자체(ss.region) 기준
    models = complete_models(model_df, ss.region) if ss.region else []
    model_key = bind("model")
    if ss[model_key] not in models:
        ss[model_key] = None
    ss.model = st.selectbox(
        f"관심 모델 ({ss.region} 지원 모델)" if ss.region else "관심 모델",
        models,
        index=None,
        placeholder="모델을 선택하세요" if models else "지자체를 먼저 선택하세요",
        disabled=not models,
        key=model_key,
    )
    if ss.region and not models:
        st.warning("이 지역은 지원 모델 정보가 없습니다")

    ss.current_efficiency = st.number_input(
        "현재 차량 연비 (km/L)",
        min_value=0.0,
        step=0.1,
        format="%.1f",
        key=bind("current_efficiency"),
    )
    ss.ev_price_manwon = st.number_input(
        "관심 전기차 가격 (만원)",
        min_value=0,
        step=100,
        help="보조금 적용 전 차량 가격입니다",
        key=bind("ev_price_manwon"),
    )
    ss.ice_price_manwon = st.number_input(
        "비교 내연기관차 가격 (만원)",
        min_value=0,
        step=100,
        help="전기차 대신 구매를 고려하는 내연기관차 가격입니다",
        key=bind("ice_price_manwon"),
    )
    ss.has_scrap = st.checkbox(
        "현재 차량 폐차 또는 매도 예정", key=bind("has_scrap")
    )


STEP_RENDERERS = {1: render_step_driving, 2: render_step_residence, 3: render_step_vehicle}


def render_input():
    st.title("🚦 전기차 신호등")
    st.caption("내 조건으로 전기차 전환 여부를 판정합니다")

    current = ss.input_step
    if current == 1:
        st.button("예시 프로필로 채우기", on_click=fill_example)

    render_step_indicator(current)
    st.subheader(f"STEP {current}  {STEPS[current - 1]}")
    STEP_RENDERERS[current]()

    st.write("")
    prev_col, next_col, _ = st.columns([1, 1, 4])
    with prev_col:
        if current > 1:
            st.button(
                "이전",
                key="nav_prev",
                on_click=go_to_step,
                args=(current - 1,),
                use_container_width=True,
            )
    with next_col:
        ready = step_ready(current)
        if current < len(STEPS):
            st.button(
                "다음",
                key="nav_next",
                type="primary",
                disabled=not ready,
                on_click=go_to_step,
                args=(current + 1,),
                use_container_width=True,
            )
        else:
            st.button(
                "판정 받기",
                key="nav_submit",
                type="primary",
                disabled=not ready,
                on_click=submit,
                use_container_width=True,
            )


# --- 결과 화면 ----------------------------------------------------------

def won(value):
    return f"{value:,.0f}원"


def manwon(value):
    return f"{(value or 0) / 10000:,.0f}"


def format_net_cost(value, ev_price, ice_price):
    """표시 전용. 계산값(음수 포함)은 그대로 두고, 0 이하면 이유를 붙인다."""
    if value > 0:
        return won(value)
    if ev_price <= ice_price:
        return "0원 (전기차가 더 저렴)"
    return "0원 (보조금으로 전액 충당)"


def won_k(value):
    """천원 단위 반올림 표시 (연간 비용 추정치)."""
    return won(round(value, -3))


def evidence_table(title, rows):
    """rows: (항목, 값, 비고) 목록"""
    st.markdown(f"**{title}**")
    st.table({
        "항목": [r[0] for r in rows],
        "값": [r[1] for r in rows],
        "비고": [r[2] for r in rows],
    })


def format_bep(years):
    if math.isinf(years):
        return "회수 불가"
    if years == 0.0:
        return "즉시 회수"
    return f"{years:.1f}년"


def render_signal(grade, reasons, explanation):
    circles = "".join(
        f"""<div style="text-align:center;margin-right:18px">
              <div style="width:64px;height:64px;border-radius:50%;
                          background:{GRADE_COLORS[g] if g == grade else OFF_COLOR}"></div>
              <div style="margin-top:6px;font-size:14px;
                          font-weight:{700 if g == grade else 400}">{GRADE_LABELS[g]}</div>
            </div>"""
        for g in ("GREEN", "YELLOW", "RED")
    )
    light_col, text_col = st.columns([1, 2])
    with light_col:
        st.markdown(
            f'<div style="display:flex;align-items:flex-start">{circles}</div>',
            unsafe_allow_html=True,
        )
    with text_col:
        # 등급(색)은 항상 judge() 결과. LLM은 문구만 담당한다.
        use_llm = not explanation["fallback"]
        headline = explanation["headline"] if use_llm else GRADE_MESSAGES[grade]
        st.markdown(
            f'<div style="font-size:32px;font-weight:700;color:{GRADE_COLORS[grade]}">'
            f"{html.escape(headline)}</div>",
            unsafe_allow_html=True,
        )
        if use_llm:
            st.write(explanation["reason"])
            if explanation["caution"]:
                st.caption(f"⚠️ {explanation['caution']}")
        elif reasons:
            st.markdown("\n".join(f"- {text}" for _, text in reasons))
        else:
            st.markdown("- 특별한 제약 사항이 없습니다")


FALLBACK_RETRY_SECONDS = 60


def explain(grade, reasons, ctx, calc_results):
    """LLM 설명을 세션에 캐싱해 위젯 조작 때마다 재호출하지 않는다.

    폴백 결과도 잠시 캐싱한다. 네트워크가 끊겼을 때 매 조작마다 타임아웃(5초)을
    기다리지 않게 하고, 60초 뒤에는 다시 시도해 키·네트워크 복구를 반영한다.
    """
    cache = ss.setdefault("llm_cache", {})
    cache_key = repr((grade, reasons, ctx, calc_results))
    cached = cache.get(cache_key)
    if cached:
        explanation, created = cached
        if not explanation["fallback"] or time.time() - created < FALLBACK_RETRY_SECONDS:
            return explanation
    with st.spinner("판정 근거를 작성하는 중..."):
        explanation = llm.generate_reason(grade, reasons, ctx, calc_results)
    cache[cache_key] = (explanation, time.time())
    return explanation


def card(title, value, note):
    st.markdown(
        f"""<div style="border:1px solid #ddd;border-radius:12px;padding:18px 20px">
              <div style="font-size:15px;color:#666">{title}</div>
              <div style="font-size:30px;font-weight:700;margin:6px 0">{value}</div>
              <div style="font-size:13px;color:#888">{note}</div>
            </div>""",
        unsafe_allow_html=True,
    )


def render_region_switcher():
    """결과 화면에서 지자체만 바꿔 즉시 재판정. 모델 미지원 지역이면 기존 결과 유지."""
    if "w_result_region" not in ss:
        ss.w_result_region = ss.region
    picked = st.selectbox(
        "지자체 변경", loader.get_regions(summary_df), key="w_result_region"
    )
    if picked == ss.region:
        return
    if ss.model in complete_models(model_df, picked):
        ss.region = picked
        ss.pop("w_region", None)  # 입력 화면으로 돌아가도 바뀐 지자체가 보이게
    else:
        st.warning(
            f"이 지역은 해당 모델을 지원하지 않습니다 ({picked}). "
            f"아래는 {ss.region} 기준 결과입니다."
        )


def render_result():
    st.title("🚦 전기차 신호등")
    render_region_switcher()
    st.caption(f"{ss.region} · {ss.model}")

    try:
        model_info = loader.get_model_info(model_df, ss.region, ss.model)
        ev_eff = calc.calc_efficiency(model_info["battery_kwh"], model_info["range_normal"])
    except (KeyError, ValueError) as e:
        st.error(f"계산할 수 없습니다: {e}")
        st.button("다시 입력하기", on_click=back_to_input)
        return

    fuel = calc.calc_fuel_saving(ss.annual_km, ss.current_efficiency, ev_eff)
    subsidy = calc.calc_subsidy(model_info, ss.has_scrap)
    co2 = calc.calc_co2(ss.annual_km, ss.current_efficiency, ev_eff)
    ev_price = ss.ev_price_manwon * 10000  # 만원 → 원
    ice_price = ss.ice_price_manwon * 10000
    price_gap = calc.calc_price_gap(ev_price, ice_price)
    bep = calc.calc_bep(price_gap, subsidy["subsidy"] or 0, fuel["saving"])
    status = loader.get_region_status(summary_df, ss.region)
    ctx = judge.JudgeContext(
        bep_years=bep["bep_years"],
        hold_years=ss.hold_years,
        home_charger=ss.home_charger,
        work_charger=ss.work_charger == "있음",  # '해당없음'은 '없음'과 동일
        housing=ss.housing,
        long_trip=ss.long_trip,
        range_cold=model_info["range_cold"],
        annual_km=ss.annual_km,
        commute_km=ss.commute_km,
    )
    grade, reasons = judge.judge(ctx)
    level, advice = judge.timing_advice(status)

    # 1) 신호등
    calc_results = {
        "fuel": fuel,
        "subsidy": subsidy,
        "co2": co2,
        "bep": bep,
        "inputs": {
            "current_efficiency": ss.current_efficiency,
            "ev_price": ev_price,
            "ice_price": ice_price,
            "price_gap": price_gap,
            "battery_kwh": model_info["battery_kwh"],
            "range_normal": model_info["range_normal"],
        },
    }
    explanation = explain(grade, reasons, ctx, calc_results)
    render_signal(grade, reasons, explanation)
    st.write("")

    # 2) 금액 카드 — 절감액과 보조금은 성격이 달라 합산하지 않는다
    left, right = st.columns(2)
    with left:
        card(
            "연간 연료비 절감",
            won_k(fuel["saving"]),  # 추정치라 카드에는 천원 단위로
            f"연 {ss.annual_km:,}km · 연비 {ss.current_efficiency}km/L 기준",
        )
    with right:
        breakdown = f"국비 {manwon(subsidy['subsidy_national'])}만 + 지방비 {manwon(subsidy['subsidy_local'])}만"
        if ss.has_scrap:
            scrap = (subsidy["scrap_national"] or 0) + (subsidy["scrap_local"] or 0)
            breakdown += f" + 전환 {manwon(scrap)}만원"
        card("예상 보조금", won(subsidy["subsidy"] or 0), breakdown)
    st.write("")

    # 3) CO2
    card(
        "연간 CO2 감축량",
        f"{co2['reduction_ton']:.2f}톤",
        f"소나무 {co2['pine_trees']:.0f}그루가 1년간 흡수하는 양",
    )
    st.write("")

    # 4) 보조금 현황
    background, border = TIMING_COLORS[level]
    if level == "unknown":
        figures = "접수율 정보 없음 · 출고잔여 정보 없음"
    else:
        remain = status["출고잔여"]
        remain_text = f"{remain:,}대" if remain is not None else "정보 없음"
        figures = f"접수율 {status['접수율']}% · 출고잔여 {remain_text}"
    st.markdown(
        f"""<div style="background:{background};border-left:6px solid {border};
                        border-radius:8px;padding:14px 18px">
              <div style="font-weight:700;margin-bottom:4px">참고 · 보조금 현황</div>
              <div>{ss.region} {figures}</div>
              <div style="margin-top:4px">{advice}</div>
            </div>""",
        unsafe_allow_html=True,
    )
    st.write("")

    # 5) 근거
    with st.expander("근거 자세히 보기"):
        subsidy_amount = subsidy["subsidy"] or 0
        evidence_table("차량 비용", [
            ("전기차 가격", won(ev_price), "사용자 입력"),
            ("보조금", f"-{subsidy_amount:,.0f}원", ""),
            ("보조금 적용 후", won(ev_price - subsidy_amount), ""),
            ("비교 내연기관차 가격", won(ice_price), "사용자 입력"),
            ("실제 추가 부담", format_net_cost(bep["net_cost"], ev_price, ice_price), ""),
        ])
        evidence_table("연간 운행 비용", [
            ("현재 차량 연간 유류비", won_k(fuel["annual_fuel_cost"]), ""),
            ("전기차 연간 충전비", won_k(fuel["annual_charge_cost"]), ""),
            ("연간 절감액", won_k(fuel["saving"]), ""),
        ])
        evidence_table("결과", [
            ("회수 기간", format_bep(bep["bep_years"]), "실제 추가 부담 ÷ 연간 절감액"),
        ])
        st.markdown(
            f"**모델 제원** · 배터리 {model_info['battery_kwh']}kWh · "
            f"상온 {model_info['range_normal']}km / 저온 {model_info['range_cold']}km"
        )
        st.markdown(
            "**데이터 출처**\n"
            "- 무공해차 통합누리집 (기후에너지환경부)\n"
            f"- 기준 시각 {base_date}\n"
            "- 유가·충전요금은 가정값이며, 차량 가격 차이는 사용자 입력값입니다"
        )
        st.caption(
            f"본 결과는 참고용입니다. 최종 확인은 관할 지자체"
            f"({status['담당부서']} {status['연락처']}) 문의"
        )

    # 6) 하단
    st.button("다시 입력하기", on_click=back_to_input)


if ss.step == "result":
    render_result()
else:
    render_input()
