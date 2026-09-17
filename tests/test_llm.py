"""LLM 연동 테스트 (실제 API 호출 없음)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import llm  # noqa: E402
from src.judge import JudgeContext  # noqa: E402

CTX = JudgeContext(
    bep_years=4.1187, hold_years=7, home_charger=True, work_charger=False,
    long_trip="월3회이상", range_cold=369, annual_km=15000,
    commute_km=40,
)
CALC = {
    "fuel": {"annual_fuel_cost": 2276785.7, "annual_charge_cost": 868571.4, "saving": 1408214.3},
    "subsidy": {"subsidy": 10200000.0},
    "co2": {"reduction_ton": 1.8468, "pine_trees": 279.8},
    "bep": {"net_cost": 5800000.0, "bep_years": 4.1187},
    "inputs": {"current_efficiency": 11.2, "battery_kwh": 83.6, "range_normal": 462,
               "ev_price": 52_000_000, "ice_price": 36_000_000, "price_gap": 16_000_000},
}
RED_REASONS = [("block", "보유 예정 3년 내 회수 불가 (BEP 4.1년)")]
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def fake_client(monkeypatch, *, text=None, error=None, stop_reason="end_turn"):
    def create(**kwargs):
        if error:
            raise error
        return SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="text", text=text)],
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda **kw: client)


def test_success(monkeypatch):
    fake_client(monkeypatch, text='{"reason": "연간 1,408,000원 절감, 4.1년 회수 예상.", "caution": ""}')
    result = llm.generate_reason("GREEN", [], CTX, CALC)
    assert result == {"headline": "전환을 권장합니다", "reason": "연간 1,408,000원 절감, 4.1년 회수 예상.",
                      "caution": "", "fallback": False}


def test_code_fence_stripped(monkeypatch):
    fake_client(monkeypatch, text='```json\n{"reason": "r", "caution": "c"}\n```')
    result = llm.generate_reason("RED", RED_REASONS, CTX, CALC)
    assert result["fallback"] is False
    assert result["caution"] == "c"


def test_headline_from_llm_is_overwritten(monkeypatch):
    fake_client(monkeypatch, text='{"headline": "충분히 타당합니다", "reason": "r", "caution": ""}')
    result = llm.generate_reason("RED", RED_REASONS, CTX, CALC)
    assert result["headline"] == "지금은 권장하지 않습니다"


def test_caution_dropped_when_no_reasons(monkeypatch):
    fake_client(monkeypatch, text='{"reason": "r", "caution": "장거리 주행 시 충전소를 확인하세요."}')
    result = llm.generate_reason("GREEN", [], CTX, CALC)
    assert result["fallback"] is False
    assert result["caution"] == ""


def test_caution_kept_when_reasons(monkeypatch):
    fake_client(monkeypatch, text='{"reason": "r", "caution": "보유 3년 내 회수가 어렵습니다."}')
    result = llm.generate_reason("RED", RED_REASONS, CTX, CALC)
    assert result["caution"] == "보유 3년 내 회수가 어렵습니다."


@pytest.mark.parametrize("reason", [
    "겨울철 주행거리가 약 20~30% 감소합니다.",
    "연간 1,500,000원 절감이 예상됩니다.",
    "약 4.2년 내 회수가 예상됩니다.",
])
def test_unknown_number_falls_back(monkeypatch, caplog, reason):
    fake_client(monkeypatch, text=f'{{"reason": "{reason}", "caution": ""}}')
    result = llm.generate_reason("GREEN", [], CTX, CALC)
    assert result["fallback"] is True
    assert "허용되지 않은 숫자" in caplog.text


def test_unknown_number_in_caution_falls_back(monkeypatch):
    fake_client(monkeypatch, text='{"reason": "r", "caution": "배터리가 10% 줄어듭니다."}')
    assert llm.generate_reason("RED", RED_REASONS, CTX, CALC)["fallback"] is True


def test_allowed_numbers_pass(monkeypatch):
    reason = ("연 15,000km 기준 1,408,000원 절감, 보조금 10,200,000원, "
              "실부담 5,800,000원, 4.1년 회수. CO2 1.85톤, 280그루, 보유 7년.")
    fake_client(monkeypatch, text=f'{{"reason": "{reason}", "caution": ""}}')
    assert llm.generate_reason("GREEN", [], CTX, CALC)["fallback"] is False


def test_numbers_outside_reasons_not_allowed(monkeypatch):
    """사유에 없는 조건(겨울철 369km, 가격 52,000,000원)은 전달하지 않으므로 인용하면 폴백."""
    for reason in ("겨울철 주행거리 369km라 불리합니다.", "전기차 가격 52,000,000원입니다."):
        fake_client(monkeypatch, text=f'{{"reason": "{reason}", "caution": ""}}')
        assert llm.generate_reason("GREEN", [], CTX, CALC)["fallback"] is True


def test_api_key_from_secrets(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "st", SimpleNamespace(secrets={"ANTHROPIC_API_KEY": "sk-cloud"}))
    assert llm._api_key() == "sk-cloud"


def test_env_key_takes_priority(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-local")
    monkeypatch.setattr(llm, "st", SimpleNamespace(secrets={"ANTHROPIC_API_KEY": "sk-cloud"}))
    assert llm._api_key() == "sk-local"


def test_secrets_error_treated_as_missing(monkeypatch):
    class BrokenSecrets:
        def get(self, *args):
            raise FileNotFoundError("No secrets.toml")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "st", SimpleNamespace(secrets=BrokenSecrets()))
    assert llm._api_key() == ""


def test_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "st", SimpleNamespace(secrets={}))
    result = llm.generate_reason("GREEN", [], CTX, CALC)
    assert result == {"headline": "전환을 권장합니다", "reason": "특별한 제약 사항이 없습니다",
                      "caution": "", "fallback": True}


@pytest.mark.parametrize("error", [
    anthropic.APITimeoutError(request=REQUEST),
    anthropic.APIConnectionError(request=REQUEST),
    anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=REQUEST), body=None
    ),
    RuntimeError("unexpected"),
])
def test_api_errors_fall_back(monkeypatch, error):
    fake_client(monkeypatch, error=error)
    reasons = [("block", "절감액이 없어 회수 불가")]
    result = llm.generate_reason("RED", reasons, CTX, CALC)
    assert result == {"headline": "지금은 권장하지 않습니다", "reason": "절감액이 없어 회수 불가.",
                      "caution": "", "fallback": True}


@pytest.mark.parametrize("text", ["이건 JSON이 아닙니다", '{"headline": "h", "caution": ""}', "[1, 2]"])
def test_bad_response_falls_back(monkeypatch, text):
    fake_client(monkeypatch, text=text)
    assert llm.generate_reason("GREEN", [], CTX, CALC)["fallback"] is True


def test_refusal_falls_back(monkeypatch):
    fake_client(monkeypatch, text="", stop_reason="refusal")
    assert llm.generate_reason("GREEN", [], CTX, CALC)["fallback"] is True


def test_numbers_passed_as_display_strings():
    message = llm._build_user_message("GREEN", [], CTX, CALC)
    for expected in ("1,408,000원", "10,200,000원", "1.85톤", "280그루", "5,800,000원", "4.1년",
                     "15,000km", "7년"):
        assert expected in message


def _conditions(reasons, **ctx_changes):
    from dataclasses import replace
    import json
    message = llm._build_user_message("RED", reasons, replace(CTX, **ctx_changes), CALC)
    return json.loads(message)["사용자 조건 (문맥 이해용, 판정 근거 아님)"]


BASE_KEYS = {"연간 주행거리", "예상 보유 기간"}


@pytest.mark.parametrize("reasons, extra_keys", [
    ([], set()),
    ([("block", "주거지·근무지 모두 충전 불가 — 공용 충전에 전적으로 의존")],
     {"주거지 충전기", "근무지 충전기"}),
    ([("warn", "주거지 충전 불가 — 근무지 충전에 의존"),
      ("warn", "출퇴근 왕복 80km + 주거지 충전 불가 — 공용 충전 의존도 높음")],
     {"주거지 충전기", "근무지 충전기", "출퇴근 왕복 거리"}),
    ([("warn", "겨울철 주행거리 330km — 장거리 시 충전 필요")],
     {"장거리 주행 빈도", "겨울철 주행거리"}),
    ([("block", "보유 예정 3년 내 회수 불가 (BEP 4.1년)")],
     {"관심 전기차 가격", "비교 내연기관차 가격", "차량 가격 차이"}),
    ([("block", "절감액이 없어 회수 불가")],
     {"관심 전기차 가격", "비교 내연기관차 가격", "차량 가격 차이"}),
    ([("warn", "연간 주행거리가 적어 절감 효과 제한적")], set()),
])
def test_only_conditions_mentioned_in_reasons(reasons, extra_keys):
    assert set(_conditions(reasons)) == BASE_KEYS | extra_keys


def test_charger_only_reason_excludes_trip_and_commute():
    keys = set(_conditions([("block", "주거지·근무지 모두 충전 불가 — 공용 충전에 전적으로 의존")],
                           home_charger=False, work_charger=False))
    assert not keys & {"장거리 주행 빈도", "출퇴근 왕복 거리", "겨울철 주행거리", "현재 차량 연비"}


# --- 지자체 공지사항 요약 ---------------------------------------------------

NOTICE = (
    "★전기승용 26.9.10. 7시 기준 약 440대 가능★\n"
    "○ 접수기간: 2026. 9. 9.(수) 10:00 ~ 2026. 12. 11.(금) 18:00\n"
    "* 출고 10일 이내 차량에 한하여 신청 가능\n"
    "* 현재 접수 폭주로 전화연결이 어렵습니다."
)
MODEL = "더 뉴 아이오닉5 2WD 롱레인지 19인치"


def test_notice_summary_success(monkeypatch):
    fake_client(monkeypatch, text='{"items": ["접수기간은 2026. 12. 11.(금) 18:00까지입니다.", '
                                  '"출고 10일 이내 차량만 신청할 수 있습니다."]}')
    assert llm.summarize_notice(NOTICE, True, MODEL) == [
        "접수기간은 2026. 12. 11.(금) 18:00까지입니다.",
        "출고 10일 이내 차량만 신청할 수 있습니다.",
    ]


def test_notice_summary_code_fence_and_max_three(monkeypatch):
    fake_client(monkeypatch, text='```json\n{"items": ["a", "b", "c", "d"]}\n```')
    assert llm.summarize_notice(NOTICE, False, MODEL) == ["a", "b", "c"]


def test_notice_summary_empty_items(monkeypatch):
    fake_client(monkeypatch, text='{"items": []}')
    assert llm.summarize_notice(NOTICE, False, MODEL) == []


def test_notice_summary_drops_item_with_unknown_number(monkeypatch, caplog):
    fake_client(monkeypatch, text='{"items": ["약 500대 남았습니다.", "출고 10일 이내 차량만 신청 가능합니다."]}')
    assert llm.summarize_notice(NOTICE, True, MODEL) == ["출고 10일 이내 차량만 신청 가능합니다."]
    assert "원문에 없는 숫자" in caplog.text


def test_notice_summary_allows_model_numbers(monkeypatch):
    fake_client(monkeypatch, text='{"items": ["아이오닉5 2WD 19인치는 출고 10일 이내에 신청해야 합니다."]}')
    assert llm.summarize_notice(NOTICE, True, MODEL) == ["아이오닉5 2WD 19인치는 출고 10일 이내에 신청해야 합니다."]


@pytest.mark.parametrize("notice", [None, "", "   "])
def test_notice_summary_no_notice_skips_call(monkeypatch, notice):
    def boom(**kw):
        raise AssertionError("API를 호출하면 안 됨")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(llm.anthropic, "Anthropic", boom)
    assert llm.summarize_notice(notice, True, MODEL) is None


@pytest.mark.parametrize("kwargs", [
    {"error": anthropic.APITimeoutError(request=REQUEST)},
    {"error": anthropic.APIConnectionError(request=REQUEST)},
    {"text": "요약할 수 없습니다"},
    {"text": '{"summary": "x"}'},
    {"text": "", "stop_reason": "refusal"},
])
def test_notice_summary_failure_returns_none(monkeypatch, kwargs):
    fake_client(monkeypatch, **kwargs)
    assert llm.summarize_notice(NOTICE, True, MODEL) is None


def test_notice_summary_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "st", SimpleNamespace(secrets={}))
    assert llm.summarize_notice(NOTICE, True, MODEL) is None


def test_timeouts_separate(monkeypatch):
    """판정 설명은 5초, 공지 요약은 12초 타임아웃으로 클라이언트를 만든다."""
    seen = []

    def factory(**kw):
        seen.append(kw["timeout"])
        create = lambda **_: SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text='{"reason": "r", "caution": "", "items": []}')],
        )
        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(llm.anthropic, "Anthropic", factory)
    llm.generate_reason("GREEN", [], CTX, CALC)
    llm.summarize_notice(NOTICE, True, MODEL)
    assert seen == [5, 12]


# --- 보조금 자격 문의 챗봇 --------------------------------------------------

RULES = "# 공통 규정\n## 우선순위 대상\n- 다자녀 가구: 자녀 2명 이상\n## 국비 가산\n- 차상위 이하 30% 추가"
CHAT_NOTICE = "★전기승용 약 440대 가능★\n* 출고 10일 이내 차량에 한하여 신청"


def chat_client(monkeypatch, text=None, error=None):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        if error:
            raise error
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    def factory(**kw):
        captured["timeout"] = kw["timeout"]
        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(llm.anthropic, "Anthropic", factory)
    return captured


def test_chat_answer_with_grounded_numbers(monkeypatch):
    captured = chat_client(monkeypatch, text="환경부 공통 지침에 따르면 차상위 이하는 30% 추가됩니다. 성남시 공지에 따르면 출고 10일 이내 차량만 신청할 수 있습니다.")
    result = llm.answer_eligibility("차상위 혜택은?", [], "성남시", CHAT_NOTICE, RULES)
    assert result["ok"] is True
    assert "30%" in result["answer"] and "10일" in result["answer"]
    assert captured["timeout"] == 15
    assert RULES in captured["system"] and CHAT_NOTICE in captured["system"] and "성남시" in captured["system"]


def test_chat_replaces_sentence_with_unknown_number(monkeypatch, caplog):
    chat_client(monkeypatch, text="다자녀 가구는 100만원을 추가로 받습니다. 자세한 기준은 지자체마다 다릅니다.")
    result = llm.answer_eligibility("다자녀 혜택은?", [], "성남시", CHAT_NOTICE, RULES)
    assert result["answer"] == "정확한 금액은 관할 지자체에 확인해 주세요. 자세한 기준은 지자체마다 다릅니다."
    assert "근거에 없는 숫자" in caplog.text and "100" in caplog.text


def test_chat_empty_rules_no_numbers_kept(monkeypatch):
    chat_client(monkeypatch, text="제공된 자료에 해당 내용이 없습니다. 관할 지자체에 확인이 필요합니다.")
    result = llm.answer_eligibility("다자녀 혜택은?", [], "성남시", CHAT_NOTICE, "# 공통 규정\n## 우선순위 대상\n")
    assert result == {"answer": "제공된 자료에 해당 내용이 없습니다. 관할 지자체에 확인이 필요합니다.", "ok": True}


def test_chat_history_limited_to_six_turns(monkeypatch):
    captured = chat_client(monkeypatch, text="답변입니다.")
    history = []
    for i in range(10):
        history += [{"role": "user", "content": f"질문{i}"}, {"role": "assistant", "content": f"답변{i}"}]
    llm.answer_eligibility("새 질문", history, "성남시", CHAT_NOTICE, RULES)
    messages = captured["messages"]
    assert len(messages) == 6 * 2 + 1
    assert messages[0] == {"role": "user", "content": "질문4"}
    assert messages[-1] == {"role": "user", "content": "새 질문"}


@pytest.mark.parametrize("kwargs", [
    {"error": anthropic.APITimeoutError(request=REQUEST)},
    {"error": anthropic.APIConnectionError(request=REQUEST)},
    {"text": ""},
])
def test_chat_failure_returns_unavailable(monkeypatch, kwargs):
    chat_client(monkeypatch, **kwargs)
    assert llm.answer_eligibility("질문", [], "성남시", CHAT_NOTICE, RULES) == {
        "answer": "일시적으로 답변할 수 없습니다.", "ok": False}


def test_chat_without_notice_or_key(monkeypatch):
    captured = chat_client(monkeypatch, text="제공된 자료에 해당 내용이 없습니다.")
    assert llm.answer_eligibility("질문", [], "의령군", None, RULES)["ok"] is True
    assert "(공지 원문 없음)" in captured["system"]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "st", SimpleNamespace(secrets={}))
    assert llm.answer_eligibility("질문", [], "성남시", CHAT_NOTICE, RULES)["ok"] is False
