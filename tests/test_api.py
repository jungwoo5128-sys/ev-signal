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
