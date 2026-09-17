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

from config import CHAT_LLM_TIMEOUT, LLM_TIMEOUT, NOTICE_LLM_TIMEOUT

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
- 판정 사유 목록에 없는 새 판단이나 조건을 추가하지 마세요.
- reason은 제공된 '판정 사유 목록'에 있는 항목과 계산 결과만 근거로 쓰세요.
  사용자 조건은 문맥 이해용으로 제공되는 것이며,
  사유 목록에 없는 조건을 판정의 이유로 언급하지 마세요.
  예: 사유 목록에 충전 환경만 있다면, 주행거리나 출퇴근 거리를
  불리한 근거로 들지 마세요.
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


def _relevant_conditions(reasons, ctx, inputs) -> dict:
    """판정 사유에 언급된 조건만 추린다. 사유 밖 조건을 LLM이 근거로 오용하지 않게 하기 위함.

    연간 주행거리·보유 기간은 계산 결과 설명에 필요해 항상 포함한다.
    """
    texts = " ".join(text for _, text in reasons)
    conditions = {
        "연간 주행거리": f"{ctx.annual_km:,}km",
        "예상 보유 기간": f"{ctx.hold_years}년",
    }
    if "회수" in texts:
        conditions["관심 전기차 가격"] = f"{inputs.get('ev_price') or 0:,.0f}원"
        conditions["비교 내연기관차 가격"] = f"{inputs.get('ice_price') or 0:,.0f}원"
        conditions["차량 가격 차이"] = f"{inputs.get('price_gap') or 0:,.0f}원"
    if "충전 불가" in texts:
        conditions["주거지 충전기"] = "있음" if ctx.home_charger else "없음"
        conditions["근무지 충전기"] = "있음" if ctx.work_charger else "없음"
    if "출퇴근" in texts:
        conditions["출퇴근 왕복 거리"] = f"{ctx.commute_km:,}km"
    if "겨울철 주행거리" in texts:
        conditions["장거리 주행 빈도"] = ctx.long_trip
        conditions["겨울철 주행거리"] = f"{ctx.range_cold}km"
    return conditions


def _build_user_message(grade, reasons, ctx, calc_results) -> str:
    """숫자는 화면 표기와 같은 문자열로 포맷해 전달한다 (LLM 재계산 방지)."""
    fuel = calc_results["fuel"]
    subsidy = calc_results["subsidy"]
    co2 = calc_results["co2"]
    bep = calc_results["bep"]
    inputs = calc_results.get("inputs", {})

    payload = {
        "판정 등급": grade,
        "판정 사유 목록": [{"심각도": s, "설명": t} for s, t in reasons],
        "사용자 조건 (문맥 이해용, 판정 근거 아님)": _relevant_conditions(reasons, ctx, inputs),
        "계산 결과": {
            "연간 연료비 절감": f"{round(fuel['saving'], -3):,.0f}원",
            "예상 보조금": f"{subsidy['subsidy'] or 0:,.0f}원",
            "보조금 차감 후 실부담": f"{bep['net_cost']:,.0f}원",
            "회수 기간": _format_bep(bep["bep_years"]),
            "연간 CO2 감축량": f"{co2['reduction_ton']:.2f}톤",
            "소나무 환산": f"{co2['pine_trees']:.0f}그루",
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


def _complete(
    api_key: str,
    system: str,
    user_message: str | None = None,
    timeout: float = LLM_TIMEOUT,
    max_tokens: int = 1024,
    messages: list[dict] | None = None,
) -> str:
    """한 번의 Messages API 호출. 실패는 예외로 올린다 (호출부에서 폴백 처리).

    단일 질문은 user_message, 대화 기록이 있으면 messages로 전달한다.
    """
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=messages or [{"role": "user", "content": user_message}],
    )
    if response.stop_reason == "refusal":
        raise ValueError("refusal")
    return "".join(b.text for b in response.content if b.type == "text")


def _log_failure(label: str, error: Exception, timeout: float = LLM_TIMEOUT) -> None:
    if isinstance(error, anthropic.AuthenticationError):
        logger.warning("%s: 잘못된 API 키", label)
    elif isinstance(error, anthropic.APITimeoutError):
        logger.warning("%s: 타임아웃 (%ss)", label, timeout)
    elif isinstance(error, anthropic.APIConnectionError):
        logger.warning("%s: 네트워크 오류", label)
    elif isinstance(error, anthropic.APIStatusError):
        logger.warning("%s: API 오류 %s", label, error.status_code)
    elif isinstance(error, (json.JSONDecodeError, ValueError)):
        logger.warning("%s: 응답 파싱 실패 (%s)", label, error)
    else:
        logger.exception("%s: 예상치 못한 오류", label)


def generate_reason(grade, reasons, ctx, calc_results) -> dict:
    """judge() 결과를 자연어로 풀어쓴다. 실패하면 사유 리스트 기반 폴백을 반환."""
    api_key = _api_key()
    if not api_key:
        logger.warning("LLM 폴백: ANTHROPIC_API_KEY 없음")
        return _fallback(grade, reasons)

    try:
        user_message = _build_user_message(grade, reasons, ctx, calc_results)
        parsed = _parse(_complete(api_key, SYSTEM_PROMPT, user_message))

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
    except Exception as e:  # 데모 중 어떤 예외도 앱 밖으로 던지지 않는다
        _log_failure("LLM 폴백", e)
    return _fallback(grade, reasons)


# --- 지자체 공지사항 요약 -------------------------------------------------

NOTICE_SYSTEM_PROMPT = """당신은 지자체 전기차 보조금 공지사항에서 구매 결정에 영향을 주는 내용만 뽑아 요약합니다.

우선 추출 대상:
- 접수 마감 여부와 재개 일정
- 대기 순번 관련 정보
- 출고 기한, 자동 취소 조건
- 서류 제출 관련 제약
- 우선순위 배정 방식

제외할 내용:
- 전화 응대 관련 안내
- 화물·승합 등 전기승용차가 아닌 차종 정보
- 단순 인사말, 사과, 감사 표현

규칙:
- 공지문에는 여러 시점의 안내가 누적되어 있을 수 있습니다.
  일반적으로 위쪽에 있는 내용이 더 최신입니다.
  같은 사안에 대해 서로 다른 내용이 있으면 위쪽(최신) 내용을 사용하고,
  아래쪽의 지난 내용은 무시하세요.
  날짜가 명시된 경우 더 나중 날짜의 안내를 우선하세요.
- 원문에 없는 내용을 추가하지 마세요.
- 원문에 적힌 사실만 옮기세요. 원문에서 추론한 결과나 결론을 쓰지 마세요.
  특히 다음을 하지 마세요:
  - 조건문을 부정형 결론으로 바꾸기
    (예: 'A하면 B 필요' → 'A 안 하면 B 불가능')
  - 원문에 없는 '불가', '제외', '탈락' 같은 단정적 표현 추가하기
  - 여러 문장을 합쳐 원문에 없는 인과관계 만들기
  각 항목은 원문의 해당 문장을 거의 그대로 옮기되, 길면 줄이기만 하세요.
- 원문의 숫자·날짜를 바꾸거나 계산하지 마세요. 원문 표기를 그대로 쓰세요.
- 사용자 조건(폐차 예정 여부, 관심 모델)과 관련된 내용을 우선하세요.
- 각 항목은 한 문장, 최대 3개.
- 해당 내용이 없으면 빈 배열을 반환하세요.

출력은 JSON만. 코드블록이나 다른 텍스트를 붙이지 마세요.
{"items": ["...", "..."]}"""

MAX_NOTICE_ITEMS = 3


def _digit_groups(text: str) -> set[str]:
    """숫자 묶음을 앞자리 0 없이 추출. '1,713' → '1713', '26.08.20' → {'26', '8', '20'}"""
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    return {str(int(g)) for g in re.findall(r"\d+", text)}


def summarize_notice(notice, has_scrap: bool, model: str):
    """공지 원문 요약 항목 리스트. 원문이 없거나 실패하면 None (섹션을 숨긴다).

    원문에 없는 숫자가 들어간 항목은 버린다.
    """
    if not notice or not str(notice).strip():
        return None
    api_key = _api_key()
    if not api_key:
        logger.warning("공지 요약 생략: ANTHROPIC_API_KEY 없음")
        return None

    user_message = json.dumps(
        {
            "사용자 조건": {
                "현재 차량 폐차·매도 예정": "예" if has_scrap else "아니오",
                "관심 모델": model,
            },
            "공지 원문": str(notice),
        },
        ensure_ascii=False,
        indent=2,
    )
    try:
        text = _complete(api_key, NOTICE_SYSTEM_PROMPT, user_message, timeout=NOTICE_LLM_TIMEOUT)
        data = json.loads(CODE_FENCE_RE.sub("", text.strip()))
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError("items 누락")
    except Exception as e:
        _log_failure("공지 요약 생략", e, timeout=NOTICE_LLM_TIMEOUT)
        return None

    allowed = _digit_groups(str(notice)) | _digit_groups(model or "")
    result = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        unknown = _digit_groups(item) - allowed
        if unknown:
            logger.warning("공지 요약 항목 제외: 원문에 없는 숫자 %s", sorted(unknown))
            continue
        result.append(item.strip())
    return result[:MAX_NOTICE_ITEMS]



# --- 보조금 자격 문의 챗봇 -------------------------------------------------
# 판정(judge)과 무관한 정보 제공용. 근거 문서(공통 규정 + 지자체 공지)에서만 답한다.

CHAT_SYSTEM_PROMPT = """당신은 전기차 보조금 자격 정보를 안내합니다.
아래 '공통 규정'과 '지자체 공지'에 있는 내용만으로 답하세요.

- 두 근거에 없는 금액, 비율, 기준을 만들어내지 마세요
- 근거에 없는 질문에는 '제공된 자료에 해당 내용이 없습니다.
  관할 지자체에 확인이 필요합니다'라고 답하세요
- 지자체마다 기준이 다른 사항은 그렇다고 명시하세요
- 금액이나 비율을 답할 때는 출처를 밝히세요
  (예: '환경부 공통 지침에 따르면', '{region} 공지에 따르면')
- 구매를 권유하거나 만류하지 마세요
- 2~4문장으로 간결하게
- 사용자의 조건이 자격 요건을 충족하는지 판단하지 마세요.
  근거에 어떤 대상이 있다고만 적혀 있고 구체적 기준이 없으면,
  그 대상이 존재한다는 사실만 전하고 기준은 지자체에 확인하도록 안내하세요.

  예: 공지에 '다자녀 가구 우선순위'만 있고 기준 인원이 없는 경우
  → (X) '자녀 3명이면 해당합니다'
  → (O) '다자녀 가구가 우선순위 대상으로 명시되어 있습니다.
         다자녀 기준은 지자체마다 다르므로 관할 지자체 확인이 필요합니다.'

  '해당합니다', '해당할 수 있습니다', '대상입니다' 같은 자격 판단 표현을
  근거 없이 쓰지 마세요.

<공통 규정>
{rules}
</공통 규정>

<지자체 공지 지자체="{region}">
{notice}
</지자체 공지>"""

CHAT_UNAVAILABLE = "일시적으로 답변할 수 없습니다."
CHAT_NUMBER_REPLACEMENT = "정확한 금액은 관할 지자체에 확인해 주세요."
CHAT_MAX_TURNS = 6
SENTENCE_RE = re.compile(r"(?<=[.!?。])\s+|\n+")


def _replace_unsupported_sentences(answer: str, allowed: set[str]) -> str:
    """근거에 없는 숫자가 든 문장을 안내 문구로 바꾼다 (공지 요약과 같은 숫자 검증)."""
    result = []
    for sentence in (s.strip() for s in SENTENCE_RE.split(answer)):
        if not sentence:
            continue
        unknown = _digit_groups(sentence) - allowed
        if unknown:
            logger.warning("챗봇 문장 대체: 근거에 없는 숫자 %s", sorted(unknown))
            sentence = CHAT_NUMBER_REPLACEMENT
        if not (result and result[-1] == sentence == CHAT_NUMBER_REPLACEMENT):
            result.append(sentence)
    return " ".join(result)


def answer_eligibility(question: str, history: list[dict], region: str, notice, rules: str) -> dict:
    """보조금 자격 질문에 근거 문서만으로 답한다.

    history: [{"role": "user"|"assistant", "content": str}, ...] (이번 질문 제외)
    반환: {"answer": str, "ok": bool}
    """
    question = (question or "").strip()
    if not question:
        return {"answer": CHAT_UNAVAILABLE, "ok": False}
    api_key = _api_key()
    if not api_key:
        logger.warning("챗봇 응답 불가: ANTHROPIC_API_KEY 없음")
        return {"answer": CHAT_UNAVAILABLE, "ok": False}

    notice_text = str(notice).strip() if notice else "(공지 원문 없음)"
    system = CHAT_SYSTEM_PROMPT.format(rules=rules or "(내용 없음)", notice=notice_text, region=region)
    recent = [m for m in history if m.get("role") in ("user", "assistant") and m.get("content")]
    messages = recent[-CHAT_MAX_TURNS * 2:] + [{"role": "user", "content": question}]
    while messages and messages[0]["role"] != "user":  # 첫 메시지는 user여야 한다
        messages = messages[1:]

    try:
        answer = _complete(api_key, system, timeout=CHAT_LLM_TIMEOUT, messages=messages).strip()
        if not answer:
            raise ValueError("빈 응답")
    except Exception as e:
        _log_failure("챗봇 응답 불가", e, timeout=CHAT_LLM_TIMEOUT)
        return {"answer": CHAT_UNAVAILABLE, "ok": False}

    # 허용 숫자: 근거 문서(규정·공지) + 사용자가 직접 말한 숫자(이번 질문과 전달한 최근 6턴 기록).
    # 사용자가 준 숫자를 되짚는 것은 정상이다 ("자녀 3명이면?" → "3명").
    allowed = _digit_groups(rules or "") | _digit_groups(notice_text) | _digit_groups(region or "")
    for message in messages:
        allowed |= _digit_groups(message["content"])
    return {"answer": _replace_unsupported_sentences(answer, allowed), "ok": True}
