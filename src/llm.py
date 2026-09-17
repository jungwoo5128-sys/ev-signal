"""LLM 연동."""

import json
import logging
import math
import os
import re
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from config import CHAT_LLM_TIMEOUT, LLM_TIMEOUT, NOTICE_LLM_TIMEOUT
from src.calc import format_km

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
- 충전기 항목은 현재 있는지 없는지만을 뜻합니다.
  '설치할 수 없다', '설치가 불가능하다'처럼 설치 가능 여부로 바꿔 쓰지 마세요.
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
        conditions["출퇴근 왕복 거리"] = f"{format_km(ctx.commute_km)}km"
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
    """환경변수(로컬은 .env, 배포 환경은 호스팅 설정)에서 API 키를 찾는다."""
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


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

# 부정 추론 금지 규칙은 틀린 예시 문장을 인용하지 않고 서술로만 금지한다.
# 틀린 문장을 (X) 예시로 넣었을 때 모델이 그대로 따라 쓰는 경우가 7회 중 3회 있었다.
CHAT_SYSTEM_PROMPT = """당신은 전기차 보조금 자격 정보를 안내합니다.
아래 '공통 규정', '지자체 공지', '충전 인프라'에 있는 내용만으로 답하세요.

- 근거에 없는 금액, 비율, 기준, 개수를 만들어내지 마세요
- 근거에 없는 질문에는 '제공된 자료에 해당 내용이 없습니다.
  관할 지자체에 확인이 필요합니다'라고 답하세요
- 지자체마다 기준이 다른 사항은 그렇다고 명시하세요
- 금액이나 비율을 답할 때는 출처를 밝히세요
  (예: '<해당 섹션의 출처명>에 따르면', '{region} 공지에 따르면')
- 출처를 밝힐 때는 해당 내용이 있는 섹션에 적힌 출처명을 사용하고,
  섹션에 출처가 없으면 규정 파일 상단에 적힌 출처명을 사용하세요.
  임의로 다른 기관명을 쓰지 마세요.
- 구매를 권유하거나 만류하지 마세요
- 2~4문장으로 간결하게
- 사용자의 조건이 자격 요건을 충족하는지 판단하지 마세요.
  근거에 어떤 대상이 있다고만 적혀 있고 구체적 기준이 없으면,
  그 대상이 존재한다는 사실만 전하고 기준은 지자체에 확인하도록 안내하세요.

  예: 근거에 '소상공인 우대'만 있고 소상공인 확인 방법이나 기준이 없는 경우,
  사업자 등록 여부나 매출 규모 같은 사용자 조건으로 해당 여부를 판단하지 마세요.
  → (O) '소상공인이 우대 대상으로 명시되어 있습니다.
         소상공인 확인 방법은 자료에 없어 관할 지자체 확인이 필요합니다.'

  '해당합니다', '해당할 수 있습니다', '대상입니다' 같은 자격 판단 표현을
  근거 없이 쓰지 마세요.
- 근거에 A가 적혀 있다는 이유로 B가 없다고 결론짓지 마세요.
  근거가 언급하지 않은 것은 '없다'가 아니라 '자료에 없다'입니다.

  예: 공지에 '출고·등록순으로 선정'이라고만 있을 때,
  선정 방식을 근거로 우선순위가 없다거나 우선순위 서류가 필요 없다고 결론짓지 마세요.
  → (O) '{region}는 출고·등록순으로 선정한다고 공지되어 있습니다.
         우선순위 적용 여부는 자료에 없어 지자체 확인이 필요합니다.'
  "~가 아닌 ~입니다", "~는 요구하지 않습니다" 같은 부정 단정은 근거에 그렇게 적혀 있을 때만 쓰세요.
- 충전 인프라 정보가 제공된 경우, 해당 지역의 충전소·충전기 수를 답할 수 있습니다.
  답변 시 기준 시각을 함께 밝히세요. (예: 충전 인프라에 적힌 'YYYY-MM-DD 기준')
  단, 제공되는 것은 지자체 전체의 개수입니다.
  특정 위치 근처나 주거지 인근 충전소는 알 수 없으므로,
  그런 질문에는 무공해차 통합누리집 충전소 찾기에서 확인하도록 안내하세요.
- 충전 인프라 정보가 '(지자체 미선택)'이면 충전소·충전기 수를 답하지 말고,
  지자체를 선택하면 해당 지역의 충전소 현황을 안내할 수 있다고 답하세요.
  지자체 선택 메뉴가 화면 어디에 있는지는 알 수 없으므로 위치를 말하지 마세요.

<공통 규정>
{rules}
</공통 규정>

<지자체 공지 지자체="{region}">
{notice}
</지자체 공지>

<충전 인프라 지자체="{region}">
{chargers}
</충전 인프라>"""

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


CHARGERS_NO_REGION = "(지자체 미선택)"
CHARGERS_MISSING = "(충전 인프라 정보 없음)"


def answer_eligibility(
    question: str, history: list[dict], region: str, notice, rules: str, chargers: str | None = None
) -> dict:
    """보조금 자격 질문에 근거 문서만으로 답한다.

    history: [{"role": "user"|"assistant", "content": str}, ...] (이번 질문 제외)
    chargers: 선택 지자체의 충전 인프라 요약(데이터가 없으면 CHARGERS_MISSING). 지자체 미선택이면 None.
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
    chargers_text = chargers.strip() if chargers else CHARGERS_NO_REGION
    system = CHAT_SYSTEM_PROMPT.format(
        rules=rules or "(내용 없음)", notice=notice_text, region=region, chargers=chargers_text
    )
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

    # 허용 숫자: 근거 문서(규정·공지·충전 인프라) + 사용자가 직접 말한 숫자(이번 질문과 전달한 최근 6턴 기록).
    # 사용자가 준 숫자를 되짚는 것은 정상이다 ("자녀 3명이면?" → "3명").
    allowed = (
        _digit_groups(rules or "") | _digit_groups(notice_text) | _digit_groups(region or "")
        | _digit_groups(chargers_text)
    )
    for message in messages:
        allowed |= _digit_groups(message["content"])
    return {"answer": _replace_unsupported_sentences(answer, allowed), "ok": True}


# --- LangGraph 챗봇용 -------------------------------------------------------
# api/chat_graph.py의 노드가 호출한다. 판정·계산은 여전히 코드가 하고, 여기서는
# (1) 질문 의도 분류, (2) 조건 변경값 추출, (3) 판정 설명 초안 1회 생성만 한다.

INTENTS = ("eligibility", "explain", "what_if", "off_topic")

INTENT_SYSTEM_PROMPT = """사용자는 전기차 전환 판정 결과를 이미 받은 상태입니다.
질문을 아래 중 하나로 분류하세요.

- eligibility: 보조금 자격·우대 대상·신청 절차·서류·지자체 공지에 대한 질문,
  지역의 충전소 개수·위치를 묻는 질문
- explain: 지금 받은 판정 결과(등급, 절감액, 회수 기간 등)의 이유를 묻는 질문
- what_if: 주행거리, 출퇴근 거리, 장거리 빈도, 충전기, 보유 기간, 차량 가격, 비교 내연기관차 연비, 폐차 여부를 바꿔 보는 질문,
  내 주거지·근무지에 충전기가 있고 없고를 가정해 결과가 어떻게 달라지는지 묻는 질문
- off_topic: 전기차 전환·보조금과 무관한 질문

충전소 개수를 묻는 것은 eligibility, 내 충전기 유무를 바꿔 보는 것은 what_if입니다.

출력은 JSON만. {"intent": "..."}"""

CHANGE_SYSTEM_PROMPT = """사용자 질문에서 '바꿔 보고 싶은 조건'만 뽑아 JSON으로 출력하세요.
질문에 없는 항목은 넣지 마세요. 값을 추정하거나 계산하지 마세요.

사용할 수 있는 키:
- annual_km: 연간 주행거리 (정수, km). "2만km" → 20000
- commute_km: 출퇴근 왕복 거리 (km)
- long_trip: "거의없음" | "월1~2회" | "월3회이상"
- home_charger: 주거지 충전기 있음 (true/false)
- work_charger: "있음" | "없음" | "해당없음"
- hold_years: 보유 기간 (3 | 5 | 7)
- current_efficiency: 비교 내연기관차 연비 (km/L)
- ev_price_manwon: 전기차 가격 (만원, 정수)
- ice_price_manwon: 비교 내연기관차 가격 (만원, 정수)
- has_scrap: 현재 차량 폐차·매도 예정 (true/false)

출력은 JSON만. 예: {"changes": {"annual_km": 20000}}"""

CHANGE_KEYS = {
    "annual_km", "commute_km", "long_trip", "home_charger", "work_charger",
    "hold_years", "current_efficiency", "ev_price_manwon", "ice_price_manwon", "has_scrap",
}


def _json(text: str) -> dict:
    # 첫 JSON 객체만 읽는다. "10년 타면요?"에서 JSON 뒤에 설명 문장을 덧붙인 응답이 있었다 (Extra data).
    data, _ = json.JSONDecoder().raw_decode(CODE_FENCE_RE.sub("", text.strip()))
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아닙니다")
    return data


def classify_intent(question: str, history: list[dict]) -> str | None:
    """질문 의도. 키 없음·실패 시 None (호출부가 기존 자격 문의로 처리)."""
    api_key = _api_key()
    if not api_key:
        return None
    recent = "\n".join(f"{m['role']}: {m['content']}" for m in history[-4:])
    user_message = f"[최근 대화]\n{recent or '(없음)'}\n\n[질문]\n{question}"
    try:
        intent = _json(_complete(api_key, INTENT_SYSTEM_PROMPT, user_message, max_tokens=50)).get("intent")
    except Exception as e:
        _log_failure("의도 분류 실패", e)
        return None
    return intent if intent in INTENTS else None


def extract_changes(question: str) -> dict | None:
    """바꿔 볼 조건. 허용 키만 남긴다.

    반환:
    - None: 호출 자체가 실패(키 없음·네트워크·인증 등) → 호출부는 CHAT_UNAVAILABLE
    - {}: 응답은 왔지만 JSON이 아니거나 바꿀 값이 없음 → 호출부는 무엇을 바꿀지 되묻는다
      ("조건 바꾸면요?"처럼 값이 없는 질문에 모델이 JSON 대신 문장으로 답하는 경우가 있었다)
    """
    api_key = _api_key()
    if not api_key:
        return None
    try:
        text = _complete(api_key, CHANGE_SYSTEM_PROMPT, question, max_tokens=200)
    except Exception as e:
        _log_failure("조건 추출 실패", e)
        return None
    try:
        changes = _json(text).get("changes")
    except ValueError as e:  # json.JSONDecodeError 포함
        logger.warning("조건 추출: 응답 파싱 실패, 바꿀 조건 없음으로 처리 (%s)", e)
        return {}
    if not isinstance(changes, dict):
        return {}
    return {k: v for k, v in changes.items() if k in CHANGE_KEYS}


def draft_reason(grade, reasons, ctx, calc_results, feedback: str = "") -> dict:
    """판정 설명을 한 번만 생성하고 검증 결과를 함께 돌려준다 (폴백으로 바꾸지 않음).

    재시도 여부는 그래프가 결정한다. feedback은 이전 시도에서 걸린 숫자 안내다.
    반환: {"result": dict | None, "unknown": list[str], "error": bool}
    """
    api_key = _api_key()
    if not api_key:
        return {"result": None, "unknown": [], "error": True}
    base_message = _build_user_message(grade, reasons, ctx, calc_results)
    # 허용 숫자는 피드백을 붙이기 전의 입력에서만 뽑는다 (피드백 속 틀린 숫자가 허용되지 않도록)
    allowed = _numbers(base_message)
    message = base_message
    if feedback:
        message += f"\n\n[이전 답변의 문제 — 반드시 고치세요]\n{feedback}"
    try:
        parsed = _parse(_complete(api_key, SYSTEM_PROMPT, message))
    except Exception as e:
        _log_failure("설명 초안 실패", e)
        return {"result": None, "unknown": [], "error": True}

    caution = parsed["caution"] if reasons else ""
    unknown = sorted(_numbers(parsed["reason"] + " " + caution) - allowed)
    result = {"headline": HEADLINES.get(grade, ""), "reason": parsed["reason"], "caution": caution, "fallback": False}
    return {"result": result, "unknown": unknown, "error": False}
