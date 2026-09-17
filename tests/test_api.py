"""FastAPI 백엔드 테스트 (LLM 호출 없음)."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api import main, service  # noqa: E402
from src import loader, llm  # noqa: E402

pytestmark = pytest.mark.skipif(
    not list(loader.DATA_DIR.glob("*.xlsx")), reason="data/에 엑셀 파일이 없음"
)

EXAMPLE = {
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


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    service._explain_cache._data.clear()
    service._notice_cache._data.clear()


def test_regions_and_models(client):
    regions = client.get("/regions").json()
    assert "성남시" in regions
    models = client.get("/models", params={"region": "성남시"}).json()
    assert EXAMPLE["model"] in models


def test_models_unknown_region_404(client):
    assert client.get("/models", params={"region": "없는시"}).status_code == 404


def test_evaluate_example_profile(client):
    body = client.post("/evaluate", json=EXAMPLE).json()
    assert body["grade"] == "GREEN"
    assert body["reasons"] == []
    assert body["cards"]["fuel_saving"]["value"].endswith("원")
    assert body["cards"]["co2"]["value"].endswith("톤")
    assert [row["label"] for row in body["evidence"]["result"]] == ["회수 기간"]
    assert body["base_date"]


def test_evaluate_matches_judge_rules(client):
    body = client.post("/evaluate", json={**EXAMPLE, "home_charger": False, "work_charger": "해당없음"}).json()
    assert body["grade"] == "RED"
    assert body["reasons"] == [
        {"severity": "block", "text": "주거지·근무지 모두 충전 불가 — 공용 충전에 전적으로 의존"}
    ]


def test_evaluate_unsupported_model_404(client):
    assert client.post("/evaluate", json={**EXAMPLE, "model": "없는 모델"}).status_code == 404


def test_evaluate_unknown_region_404(client):
    assert client.post("/evaluate", json={**EXAMPLE, "region": "없는시"}).status_code == 404


@pytest.mark.parametrize("change", [
    {"annual_km": 0},
    {"current_efficiency": 0},
    {"hold_years": 4},
    {"long_trip": "매일"},
])
def test_evaluate_invalid_input_422(client, change):
    assert client.post("/evaluate", json={**EXAMPLE, **change}).status_code == 422


def test_explain_falls_back_without_key(client):
    body = client.post("/explain", json=EXAMPLE).json()
    assert body == {"headline": "전환을 권장합니다", "reason": "특별한 제약 사항이 없습니다",
                    "caution": "", "fallback": True}


def test_notice_summary_without_key_is_none(client):
    body = client.post("/notice-summary", json={
        "region": EXAMPLE["region"], "model": EXAMPLE["model"], "has_scrap": True,
    }).json()
    assert body == {"items": None}


def test_explain_cache_retries_failure_after_window(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "generate_reason", lambda *a: calls.append(1) or {"fallback": True})
    store = service.load_store()
    inp = service.EvaluateInput(**EXAMPLE)
    service.explain(store, inp)
    service.explain(store, inp)
    assert len(calls) == 1
    now = service.time.time()
    monkeypatch.setattr(service.time, "time", lambda: now + service.FALLBACK_RETRY_SECONDS + 1)
    service.explain(store, inp)
    assert len(calls) == 2


# --- 챗봇 ---------------------------------------------------------------------

def test_chat_passes_evidence_and_trims_history(client, monkeypatch):
    captured = {}

    def fake_answer(question, history, region, notice, rules):
        captured.update(question=question, history=history, region=region, notice=notice, rules=rules)
        return {"answer": "무공해차 통합누리집 공통 안내에 따르면 국비 최대 650만원입니다.", "ok": True}

    monkeypatch.setattr(llm, "answer_eligibility", fake_answer)
    history = []
    for i in range(10):
        history += [{"role": "user", "content": f"질문{i}"}, {"role": "assistant", "content": f"답변{i}"}]
    res = client.post("/chat", json={"region": "성남시", "question": "국비 최대?", "history": history})

    assert res.status_code == 200
    assert res.json() == {"answer": "무공해차 통합누리집 공통 안내에 따르면 국비 최대 650만원입니다."}
    assert captured["question"] == "국비 최대?" and captured["region"] == "성남시"
    assert len(captured["history"]) == 12 and captured["history"][0]["content"] == "질문4"
    assert "성남시" in captured["notice"]
    assert captured["rules"] == loader.load_subsidy_rules() and captured["rules"]


def test_chat_without_llm_returns_unavailable(client):
    res = client.post("/chat", json={"region": "성남시", "question": "다자녀 혜택은?", "history": []})
    assert res.status_code == 200
    assert res.json() == {"answer": "일시적으로 답변할 수 없습니다."}


def test_chat_unknown_region_404(client):
    assert client.post("/chat", json={"region": "없는시", "question": "질문"}).status_code == 404


@pytest.mark.parametrize("body", [
    {"region": "성남시", "question": ""},
    {"region": "성남시", "question": "질문", "history": [{"role": "system", "content": "x"}]},
])
def test_chat_invalid_input_422(client, body):
    assert client.post("/chat", json=body).status_code == 422


def test_chat_without_region_uses_common_rules_only(client, monkeypatch):
    captured = {}

    def fake_answer(question, history, region, notice, rules):
        captured.update(region=region, notice=notice, rules=rules)
        return {"answer": "공통 안내에 따르면 650만원입니다.", "ok": True}

    monkeypatch.setattr(llm, "answer_eligibility", fake_answer)
    for body in ({"question": "국비 최대?"}, {"region": None, "question": "국비 최대?"}, {"region": "", "question": "국비 최대?"}):
        res = client.post("/chat", json=body)
        assert res.status_code == 200
        assert captured == {"region": "지자체 미선택", "notice": None, "rules": loader.load_subsidy_rules()}


def test_contact(client):
    assert client.get("/contact", params={"region": "성남시"}).json() == {"contact": "기후에너지과 031-729-3162"}
    assert client.get("/contact", params={"region": "없는시"}).status_code == 404


# --- 주행거리 입력 방식 ------------------------------------------------------

def _running(body):
    return {row["label"]: (row["value"], row["note"]) for row in body["evidence"]["running_cost"]}


@pytest.mark.parametrize("extra", [{}, {"distance_mode": "annual"}])
def test_evaluate_annual_mode_keeps_baseline(client, extra):
    body = client.post("/evaluate", json={**EXAMPLE, **extra}).json()
    assert body["cards"]["fuel_saving"]["value"] == "1,609,000원"
    assert body["cards"]["subsidy"]["value"] == "10,200,000원"
    assert body["cards"]["co2"]["value"] == "1.85톤"
    assert body["evidence"]["result"][0]["value"] == "3.6년"
    assert _running(body)["연간 주행거리"] == ("15,000km", "직접 입력")
    assert body["driving"] == {"mode": "annual", "annual_km": 15000}
    assert body["cards"]["fuel_saving"]["note"].startswith("연 15,000km")


@pytest.mark.parametrize("commute, long_trip, expected", [
    (40, "월3회이상", "17,400km"),
    (30, "거의없음", "9,800km"),
])
def test_evaluate_commute_mode_converts(client, commute, long_trip, expected):
    body = client.post("/evaluate", json={
        **EXAMPLE, "distance_mode": "commute", "commute_km": commute, "long_trip": long_trip,
    }).json()
    weekend = {"월3회이상": "7,000", "거의없음": "2,000"}[long_trip]
    assert _running(body)["연간 주행거리"] == (expected, f"출퇴근 {commute}km × 주 5일 × 52주 + 주말·기타 {weekend}km")
    assert body["driving"]["mode"] == "commute"
    assert body["cards"]["fuel_saving"]["note"].startswith(f"연 {expected}")


def test_commute_rule_only_in_commute_mode(client):
    commute_warn = {"severity": "warn", "text": "출퇴근 왕복 70km + 주거지 충전 불가 — 공용 충전 의존도 높음"}
    base = {**EXAMPLE, "home_charger": False, "work_charger": "있음", "commute_km": 70}

    annual = client.post("/evaluate", json={**base, "distance_mode": "annual"}).json()
    assert commute_warn not in annual["reasons"]
    assert not any("출퇴근" in r["text"] for r in annual["reasons"])

    commute = client.post("/evaluate", json={**base, "distance_mode": "commute"}).json()
    assert commute_warn in commute["reasons"]


@pytest.mark.parametrize("body", [
    {**EXAMPLE, "distance_mode": "annual", "annual_km": None},
    {**{k: v for k, v in EXAMPLE.items() if k != "commute_km"}, "distance_mode": "commute"},
    {**EXAMPLE, "distance_mode": "weekly"},
])
def test_distance_mode_validation_422(client, body):
    assert client.post("/evaluate", json=body).status_code == 422
