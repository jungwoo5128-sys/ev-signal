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
    bep_years=4.1187, hold_years=7, home_charger=True, work_charger=False, housing="아파트",
    long_trip="월3회이상", range_cold=369, annual_km=15000,
    commute_km=40,
)
CALC = {
    "fuel": {"annual_fuel_cost": 2276785.7, "annual_charge_cost": 868571.4, "saving": 1408214.3},
    "subsidy": {"subsidy": 10200000.0},
    "co2": {"reduction_ton": 1.8468, "pine_trees": 279.8},
    "bep": {"net_cost": 5800000.0, "bep_years": 4.1187},
    "inputs": {"current_efficiency": 11.2, "battery_kwh": 83.6, "range_normal": 462,
               "price_gap": 16_000_000},
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
    reason = ("연 15,000km, 연비 11.2km/L 기준 1,408,000원 절감, 보조금 10,200,000원, "
              "실부담 5,800,000원, 4.1년 회수. 83.6kWh·462km/369km, CO2 1.85톤, 280그루, 보유 7년.")
    fake_client(monkeypatch, text=f'{{"reason": "{reason}", "caution": ""}}')
    assert llm.generate_reason("GREEN", [], CTX, CALC)["fallback"] is False


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
                     "16,000,000원"):
        assert expected in message
