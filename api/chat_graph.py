"""LangGraph 챗봇 흐름.

질문 → 의도 분류 → (자격 문의 | 판정 설명 | 조건 변경 | 주제 이탈) → 응답

- 등급·계산은 여전히 코드(service._evaluate, judge)가 정한다.
- 자격·충전소 문의는 기존 service.chat()(→ llm.answer_eligibility)을 그대로 쓴다.
- 판정 설명은 숫자 검증에 걸리면 최대 MAX_RETRIES번 다시 생성하고,
  그래도 안 되면 규칙 사유로 만든 안전 템플릿(llm._fallback)으로 답한다.
- 판정 결과(profile)가 없거나 API 키가 없으면 기존과 똑같이 자격 문의로 처리한다.
"""

from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from api import service
from src import llm

MAX_RETRIES = 2

GRADE_NAMES = {"GREEN": "초록(추천)", "YELLOW": "노랑(조건부)", "RED": "빨강(비추천)"}

CHANGE_LABELS = {
    "annual_km": "연간 주행거리",
    "commute_km": "출퇴근 왕복 거리",
    "long_trip": "장거리 주행",
    "home_charger": "주거지 충전기",
    "work_charger": "근무지 충전기",
    "hold_years": "보유 기간",
    "current_efficiency": "내연기관차 연비",
    "ev_price_manwon": "전기차 가격",
    "ice_price_manwon": "내연기관차 가격",
    "has_scrap": "폐차·매도 예정",
}

OFF_TOPIC_ANSWER = (
    "저는 전기차 전환 판정과 보조금 자격을 안내하는 환경이예요. "
    "판정 이유, 조건을 바꿨을 때의 결과, 보조금 자격을 물어봐 주세요."
)
NO_CHANGE_ANSWER = "어떤 조건을 바꿔 볼지 알려주세요. 예: '연간 주행거리가 2만km면 어떻게 돼요?'"
INVALID_CHANGE_ANSWER = "바꾸려는 값을 계산에 쓸 수 없어요. {detail} 다른 값으로 다시 물어봐 주세요."


class ChatState(TypedDict, total=False):
    # 입력
    store: service.Store
    question: str
    history: list[dict]
    region: str | None
    profile: service.EvaluateInput | None
    # 진행
    intent: str
    changes: dict
    target: service.EvaluateInput  # 설명할 조건 (조건 변경이면 바뀐 조건)
    evaluation: service.Evaluation
    before: dict  # 조건 변경 전 표시값
    after: dict  # 조건 변경 후 표시값
    draft: dict | None
    unknown: list[str]
    retries: int
    # 출력
    answer: str
    profile_changes: dict


# --- 노드 ---------------------------------------------------------------------

def classify(state: ChatState) -> ChatState:
    # 판정 결과가 없으면 설명·조건 변경을 할 수 없으므로 분류하지 않는다 (기존 동작 유지)
    if state.get("profile") is None:
        return {"intent": "eligibility"}
    intent = llm.classify_intent(state["question"], state.get("history", []))
    return {"intent": intent or "eligibility"}


def eligibility(state: ChatState) -> ChatState:
    inp = service.ChatInput(region=state.get("region"), question=state["question"], history=state.get("history", []))
    return {"answer": service.chat(state["store"], inp)["answer"]}


def prepare_explain(state: ChatState) -> ChatState:
    target = state["profile"]
    return {"target": target, "evaluation": service._evaluate(state["store"], target), "retries": 0, "unknown": []}


def extract(state: ChatState) -> ChatState:
    changes = llm.extract_changes(state["question"])
    if not changes:
        return {"changes": {}, "answer": NO_CHANGE_ANSWER if changes == {} else llm.CHAT_UNAVAILABLE}

    if "annual_km" in changes:
        changes["distance_mode"] = "annual"
    elif "commute_km" in changes:
        changes["distance_mode"] = "commute"

    merged = {**state["profile"].model_dump(), **changes}
    try:
        target = service.EvaluateInput(**merged)  # 범위·선택지 검증 (예: 보유 기간 3/5/7)
        evaluation = service._evaluate(state["store"], target)
    except (ValidationError, ValueError, KeyError) as e:
        detail = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
        return {"changes": {}, "answer": INVALID_CHANGE_ANSWER.format(detail=detail)}

    return {
        "changes": changes,
        "target": target,
        "evaluation": evaluation,
        "before": service.evaluate(state["store"], state["profile"]),
        "after": service.evaluate(state["store"], target),
        "retries": 0,
        "unknown": [],
    }


def write_reason(state: ChatState) -> ChatState:
    ev = state["evaluation"]
    feedback = ""
    if state.get("unknown"):
        feedback = f"입력에 없는 숫자 {', '.join(state['unknown'])}를 썼습니다. 입력에 적힌 숫자 문자열만 그대로 쓰세요."
    out = llm.draft_reason(ev.grade, ev.reasons, ev.ctx, ev.calc_results, feedback)
    if out["error"]:
        return {"draft": None, "unknown": [], "retries": MAX_RETRIES + 1}  # 네트워크 오류 등은 재시도하지 않음
    return {"draft": out["result"], "unknown": out["unknown"], "retries": state.get("retries", 0) + (1 if out["unknown"] else 0)}


def fallback(state: ChatState) -> ChatState:
    ev = state["evaluation"]
    return {"draft": llm._fallback(ev.grade, ev.reasons)}


def respond_explain(state: ChatState) -> ChatState:
    d = state["draft"]
    parts = [f"{d['headline']}.", d["reason"].rstrip(".") + "."]
    if d.get("caution"):
        parts.append(d["caution"])

    if state.get("changes"):
        b, a = state["before"], state["after"]
        changed = ", ".join(CHANGE_LABELS[k] for k in state["changes"] if k in CHANGE_LABELS)
        grade = (
            f"{GRADE_NAMES[b['grade']]} → {GRADE_NAMES[a['grade']]}"
            if b["grade"] != a["grade"] else f"{GRADE_NAMES[a['grade']]} 그대로"
        )
        summary = (
            f"{changed} 조건을 바꿔 다시 계산했어요. 판정은 **{grade}**입니다. "
            f"연간 절감액 {b['cards']['fuel_saving']['value']} → {a['cards']['fuel_saving']['value']}, "
            f"회수 기간 {b['cards']['payback']['value']} → {a['cards']['payback']['value']}."
        )
        return {"answer": summary + " " + " ".join(parts), "profile_changes": state["changes"]}

    return {"answer": " ".join(parts)}


def off_topic(state: ChatState) -> ChatState:
    return {"answer": OFF_TOPIC_ANSWER}


# --- 분기 ---------------------------------------------------------------------

def route_intent(state: ChatState) -> Literal["eligibility", "prepare_explain", "extract", "off_topic"]:
    return {
        "explain": "prepare_explain",
        "what_if": "extract",
        "off_topic": "off_topic",
    }.get(state["intent"], "eligibility")


def route_extract(state: ChatState) -> Literal["write_reason", "__end__"]:
    return "write_reason" if state.get("changes") else END


def route_verify(state: ChatState) -> Literal["respond_explain", "write_reason", "fallback"]:
    if state.get("draft") and not state.get("unknown"):
        return "respond_explain"  # 검증 통과
    if state.get("retries", 0) <= MAX_RETRIES and state.get("draft"):
        return "write_reason"  # 숫자 불일치 → 다시 생성
    return "fallback"  # 재시도 초과 또는 호출 실패


def build_graph():
    g = StateGraph(ChatState)
    g.add_node("classify", classify)
    g.add_node("eligibility", eligibility)
    g.add_node("prepare_explain", prepare_explain)
    g.add_node("extract", extract)
    g.add_node("write_reason", write_reason)
    g.add_node("fallback", fallback)
    g.add_node("respond_explain", respond_explain)
    g.add_node("off_topic", off_topic)

    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route_intent)
    g.add_edge("prepare_explain", "write_reason")
    g.add_conditional_edges("extract", route_extract)
    g.add_conditional_edges("write_reason", route_verify)
    g.add_edge("fallback", "respond_explain")
    g.add_edge("eligibility", END)
    g.add_edge("respond_explain", END)
    g.add_edge("off_topic", END)
    return g.compile()


GRAPH = build_graph()


class GraphChatInput(service.ChatInput):
    profile: service.EvaluateInput | None = None  # 판정 결과 화면에서만 전달


def run(store: service.Store, inp: GraphChatInput) -> dict:
    out = GRAPH.invoke({
        "store": store,
        "question": inp.question,
        "history": [m.model_dump() for m in inp.history],
        "region": inp.region,
        "profile": inp.profile,
    })
    result = {"answer": out["answer"]}
    if out.get("profile_changes"):
        result["profile_changes"] = out["profile_changes"]
    return result
