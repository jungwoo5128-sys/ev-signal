"""LLM 연동."""

import json
import logging
import math
import os
import re
from pathlib import Path

import anthropic
import streamlit as st
from dotenv import load_dotenv

from config import LLM_TIMEOUT

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

HEADLINES = {
    "GREEN": "전환을 권장합니다",
    "YELLOW": "조건을 확인한 뒤 결정하세요",
    "RED": "지금은 권장하지 않습니다",
}
NO_REASON = "특별한 제약 사항이 없습니다"

SYSTEM_PROMPT = """당신은 전기차 전환 판정 결과를 사용자에게 설명하는 역할입니다.

규칙:
- 주어진 등급을 절대 바꾸지 마세요. 등급과 사유는 이미 규칙 엔진이 확정했습니다.
- 사유 리스트에 없는 새 판단이나 조건을 추가하지 마세요.
- reason에는 제공된 숫자만 사용하고, 새로운 수치를 계산하거나 추정하지 마세요.
- 숫자는 입력에 적힌 문자열을 그대로 인용하세요. 반올림하거나 단위를 바꾸지 마세요.
- 단정형을 쓰지 말고 권고형을 사용하세요. ("사세요" ✗ / "권장합니다" ○)
- 구매를 부추기거나 만류하지 말고, 사실과 계산 결과만 전달하세요.

출력 형식:
- reason: 2~3문장, 계산 결과의 숫자를 포함
- caution: 한 문장. caution은 제공된 사유 목록에 있는 항목만 다루세요.
  사유 목록이 비어 있으면 caution을 빈 문자열로 반환하세요.

출력은 JSON만. 코드블록이나 다른 텍스트를 붙이지 마세요.
{"reason": "...", "caution": "..."}"""

CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _fallback(grade: str, reasons: list[tuple[str, str]]) -> dict:
    texts = [text for _, text in reasons]
    return {
        "headline": HEADLINES.get(grade, ""),
        "reason": ". ".join(texts) + "." if texts else NO_REASON,
        "caution": "",
        "fallback": True,
    }


def _format_bep(years: float) -> str:
    if math.isinf(years):
        return "회수 불가"
    if years == 0.0:
        return "즉시 회수"
    return f"{years:.1f}년"


def _build_user_message(grade, reasons, ctx, calc_results) -> str:
    """숫자는 화면 표기와 같은 문자열로 포맷해 전달한다 (LLM 재계산 방지)."""
    fuel = calc_results["fuel"]
    subsidy = calc_results["subsidy"]
    co2 = calc_results["co2"]
    bep = calc_results["bep"]
    inputs = calc_results.get("inputs", {})

    payload = {
        "등급": grade,
        "사유": [{"심각도": s, "설명": t} for s, t in reasons] or [],
        "사용자 조건": {
            "연간 주행거리": f"{ctx.annual_km:,}km",
            "현재 차량 연비": f"{inputs.get('current_efficiency')}km/L",
            "예상 보유 기간": f"{ctx.hold_years}년",
            "주거지 충전기": "있음" if ctx.home_charger else "없음",
            "근무지 충전기": "있음" if ctx.work_charger else "없음",
            "주거 형태": ctx.housing,
            "장거리 주행 빈도": ctx.long_trip,
        },
        "차량 제원": {
            "배터리": f"{inputs.get('battery_kwh')}kWh",
            "상온 주행거리": f"{inputs.get('range_normal')}km",
            "겨울철 주행거리": f"{ctx.range_cold}km",
        },
        "계산 결과": {
            "연간 연료비 절감": f"{round(fuel['saving'], -3):,.0f}원",
            "예상 보조금": f"{subsidy['subsidy'] or 0:,.0f}원",
            "연간 CO2 감축량": f"{co2['reduction_ton']:.2f}톤",
            "소나무 환산": f"{co2['pine_trees']:.0f}그루",
            "보조금 차감 후 실부담": f"{bep['net_cost']:,.0f}원",
            "회수 기간": _format_bep(bep["bep_years"]),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _numbers(text: str) -> set[str]:
    """문장 속 숫자를 쉼표 없는 형태로 추출. '1,408,000원' → '1408000'."""
    return {n.replace(",", "") for n in NUMBER_RE.findall(text)}


def _parse(text: str) -> dict:
    data = json.loads(CODE_FENCE_RE.sub("", text.strip()))
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아닙니다")
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason 누락")
    caution = data.get("caution")
    return {
        "reason": reason.strip(),
        "caution": caution.strip() if isinstance(caution, str) else "",
    }


def _api_key() -> str:
    """로컬 환경변수(.env) → Streamlit Cloud secrets 순서로 API 키를 찾는다."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    try:
        # secrets.toml이 없으면 접근 자체가 예외를 던질 수 있다
        return str(st.secrets.get("ANTHROPIC_API_KEY", "") or "").strip()
    except Exception:
        return ""


def generate_reason(grade, reasons, ctx, calc_results) -> dict:
    """judge() 결과를 자연어로 풀어쓴다. 실패하면 사유 리스트 기반 폴백을 반환."""
    api_key = _api_key()
    if not api_key:
        logger.warning("LLM 폴백: ANTHROPIC_API_KEY 없음")
        return _fallback(grade, reasons)

    try:
        user_message = _build_user_message(grade, reasons, ctx, calc_results)
        client = anthropic.Anthropic(
            api_key=api_key, timeout=LLM_TIMEOUT, max_retries=0
        )
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        if response.stop_reason == "refusal":
            raise ValueError("refusal")
        text = "".join(b.text for b in response.content if b.type == "text")
        parsed = _parse(text)

        # 등급 결론 문구는 코드가 고정. LLM 응답의 headline은 요청하지도, 쓰지도 않는다.
        # judge()가 제약 없음으로 판정했으면 LLM이 만든 caution은 버린다.
        caution = parsed["caution"] if reasons else ""

        # 입력으로 준 숫자 외의 수치가 섞이면 폴백
        allowed = _numbers(user_message)
        unknown = _numbers(parsed["reason"] + " " + caution) - allowed
        if unknown:
            logger.warning("LLM 폴백: 허용되지 않은 숫자 %s", sorted(unknown))
            return _fallback(grade, reasons)

        return {
            "headline": HEADLINES.get(grade, ""),
            "reason": parsed["reason"],
            "caution": caution,
            "fallback": False,
        }
    except anthropic.AuthenticationError:
        logger.warning("LLM 폴백: 잘못된 API 키")
    except anthropic.APITimeoutError:
        logger.warning("LLM 폴백: 타임아웃 (%ss)", LLM_TIMEOUT)
    except anthropic.APIConnectionError:
        logger.warning("LLM 폴백: 네트워크 오류")
    except anthropic.APIStatusError as e:
        logger.warning("LLM 폴백: API 오류 %s", e.status_code)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("LLM 폴백: 응답 파싱 실패 (%s)", e)
    except Exception:  # 데모 중 어떤 예외도 앱 밖으로 던지지 않는다
        logger.exception("LLM 폴백: 예상치 못한 오류")
    return _fallback(grade, reasons)
