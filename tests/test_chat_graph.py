"""LangGraph 챗봇 흐름 테스트 (실제 LLM 호출 없음)."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api import chat_graph, main  # noqa: E402
from src import llm, loader  # noqa: E402

pytestmark = pytest.mark.skipif(
    not list(loader.DATA_DIR.glob("*.xlsx")), reason="data/에 엑셀 파일이 없음"
)

PROFILE = {
    "distance_mode": "annual",
    "annual_km": 15000,
    "commute_km": 40,
    "long_trip": "월3회이상",
    "region": "성남시",
    "home_charger": True,
    "work_charger": "없음",
    "hold_years": 7,
    "model": "더 뉴 아이오닉5 2WD 롱레인지 19인치",
    "current_efficiency": 11.2,
    "ev_price_manwon": 5200,
    "ice_price_manwon": 3600,
    "has_scrap": True,
}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def fake_llm(monkeypatch, *, intent="explain", changes=None, reasons=("판정 사유를 확인하세요.",)):
    """system 프롬프트로 어떤 노드의 호출인지 구분해 준비된 응답을 돌려준다."""
    calls = {"intent": 0, "change": 0, "reason": 0}
    reason_iter = iter(reasons)

    def create(**kwargs):
        system = kwargs["system"]
        if system == llm.INTENT_SYSTEM_PROMPT:
            calls["intent"] += 1
            text = intent if intent.startswith("{") or intent == "not json" else json.dumps({"intent": intent})
        elif system == llm.CHANGE_SYSTEM_PROMPT:
            calls["change"] += 1
            text = json.dumps({"changes": changes or {}})
        elif system == llm.SYSTEM_PROMPT:
            calls["reason"] += 1
            text = json.dumps({"reason": next(reason_iter), "caution": ""}, ensure_ascii=False)
        else:
            raise AssertionError("예상하지 못한 호출")
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda **kw: client)
    return calls


def ask(client, question, profile=PROFILE):
    body = {"region": "성남시", "question": question, "history": []}
    if profile is not None:
        body["profile"] = profile
    res = client.post("/chat", json=body)
    assert res.status_code == 200
    return res.json()


def test_without_profile_skips_classification(client, monkeypatch):
    calls = fake_llm(monkeypatch)
    monkeypatch.setattr(llm, "answer_eligibility", lambda *a, **kw: {"answer": "자격 안내", "ok": True})
    assert ask(client, "다자녀 혜택은?", profile=None) == {"answer": "자격 안내"}
    assert calls["intent"] == 0


def test_eligibility_route_uses_existing_chat(client, monkeypatch):
    fake_llm(monkeypatch, intent="eligibility")
    monkeypatch.setattr(llm, "answer_eligibility", lambda *a, **kw: {"answer": "자격 안내", "ok": True})
    assert ask(client, "다자녀 혜택은?") == {"answer": "자격 안내"}


def test_classification_failure_falls_back_to_eligibility(client, monkeypatch):
    fake_llm(monkeypatch, intent="not json")
    monkeypatch.setattr(llm, "answer_eligibility", lambda *a, **kw: {"answer": "자격 안내", "ok": True})
    assert ask(client, "질문") == {"answer": "자격 안내"}


def test_explain_passes_verification(client, monkeypatch):
    calls = fake_llm(monkeypatch, reasons=["충전 환경과 절감 효과를 함께 보세요."])
    out = ask(client, "왜 이런 판정이 나왔어요?")
    assert "충전 환경과 절감 효과를 함께 보세요." in out["answer"]
    assert "profile_changes" not in out
    assert calls["reason"] == 1


def test_explain_retries_after_unknown_number(client, monkeypatch):
    calls = fake_llm(monkeypatch, reasons=["연간 999원 절감됩니다.", "절감 효과가 있습니다."])
    out = ask(client, "왜요?")
    assert "절감 효과가 있습니다." in out["answer"] and "999" not in out["answer"]
    assert calls["reason"] == 2


def test_explain_uses_safe_template_after_max_retries(client, monkeypatch):
    calls = fake_llm(monkeypatch, reasons=["999원"] * 5)
    out = ask(client, "왜요?")
    assert calls["reason"] == chat_graph.MAX_RETRIES + 1
    assert "999" not in out["answer"]
    assert out["answer"].startswith(tuple(llm.HEADLINES.values()))


def test_what_if_recalculates_and_returns_changes(client, monkeypatch):
    fake_llm(monkeypatch, intent="what_if", changes={"annual_km": 30000}, reasons=["주행이 많아 절감 효과가 커집니다."])
    out = ask(client, "1년에 3만km 타면요?")
    assert out["profile_changes"] == {"annual_km": 30000, "distance_mode": "annual"}
    assert "연간 주행거리" in out["answer"] and "→" in out["answer"]


def test_what_if_invalid_value_asks_again(client, monkeypatch):
    calls = fake_llm(monkeypatch, intent="what_if", changes={"hold_years": 10})
    out = ask(client, "10년 타면요?")
    assert "profile_changes" not in out
    assert "다시 물어봐" in out["answer"]
    assert calls["reason"] == 0


def test_what_if_without_changes(client, monkeypatch):
    fake_llm(monkeypatch, intent="what_if", changes={})
    assert ask(client, "조건 바꾸면요?") == {"answer": chat_graph.NO_CHANGE_ANSWER}


def test_off_topic(client, monkeypatch):
    fake_llm(monkeypatch, intent="off_topic")
    assert ask(client, "점심 뭐 먹지?") == {"answer": chat_graph.OFF_TOPIC_ANSWER}


def test_eligibility_route_keeps_charger_evidence(client, monkeypatch):
    fake_llm(monkeypatch, intent="eligibility")
    captured = {}

    def fake_answer(question, history, region, notice, rules, chargers=None):
        captured["chargers"] = chargers
        return {"answer": "충전소 안내", "ok": True}

    monkeypatch.setattr(llm, "answer_eligibility", fake_answer)
    assert ask(client, "우리 지역 충전소는 얼마나 있나요?") == {"answer": "충전소 안내"}
    assert captured["chargers"]  # 결과 화면에서도 충전 인프라 근거가 그대로 전달된다
