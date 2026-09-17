export type Grade = "GREEN" | "YELLOW" | "RED";
export type LongTrip = "거의없음" | "월1~2회" | "월3회이상";
export type WorkCharger = "있음" | "없음" | "해당없음";
export type HoldYears = 3 | 5 | 7;

export interface Profile {
  annual_km: number;
  commute_km: number;
  long_trip: LongTrip;
  region: string | null;
  home_charger: boolean;
  work_charger: WorkCharger;
  hold_years: HoldYears;
  model: string | null;
  current_efficiency: number;
  ev_price_manwon: number;
  ice_price_manwon: number;
  has_scrap: boolean;
}

export interface Row {
  label: string;
  value: string;
  note: string;
}

export interface Card {
  value: string;
  note: string;
}

export interface Evaluation {
  grade: Grade;
  reasons: { severity: "block" | "warn"; text: string }[];
  cards: { fuel_saving: Card; subsidy: Card; co2: Card };
  status: {
    level: "ok" | "urgent" | "over" | "closed" | "unknown";
    advice: string;
    figures: string;
    has_notice: boolean;
    notice: string | null;
    contact: string | null;
  };
  evidence: { vehicle_cost: Row[]; running_cost: Row[]; result: Row[]; spec: string };
  base_date: string;
}

export interface Explanation {
  headline: string;
  reason: string;
  caution: string;
  fallback: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().then((d) => d?.detail).catch(() => null);
    throw new ApiError(res.status, typeof detail === "string" ? detail : "요청에 실패했습니다");
  }
  return res.json();
}

export const api = {
  meta: () => request<{ base_date: string }>("/meta"),
  regions: () => request<string[]>("/regions"),
  models: (region: string) => request<string[]>(`/models?region=${encodeURIComponent(region)}`),
  evaluate: (p: Profile) => request<Evaluation>("/evaluate", p),
  explain: (p: Profile) => request<Explanation>("/explain", p),
  noticeSummary: (p: Profile) =>
    request<{ items: string[] | null }>("/notice-summary", {
      region: p.region,
      model: p.model,
      has_scrap: p.has_scrap,
    }),
  chat: (region: string, question: string, history: ChatMessage[]) =>
    request<{ answer: string }>("/chat", { region, question, history }),
};
