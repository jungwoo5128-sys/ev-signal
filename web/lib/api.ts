export type Grade = "GREEN" | "YELLOW" | "RED";
export type LongTrip = "거의없음" | "월1~2회" | "월3회이상";
export type WorkCharger = "있음" | "없음" | "해당없음";
export type HoldYears = 3 | 5 | 7;
/** annual: 연간 주행거리 직접 입력 / commute: 출퇴근 거리로 환산 */
export type DistanceMode = "annual" | "commute";

export interface Profile {
  distance_mode: DistanceMode;
  annual_km: number;
  commute_km: number;
  long_trip: LongTrip;
  region: string | null;
  home_charger: boolean;
  work_charger: WorkCharger;
  hold_years: HoldYears;
  model: string | null;
  current_efficiency: number;
  /** 비어 있으면 null (판정 버튼 비활성화) */
  ev_price_manwon: number | null;
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
  cards: { fuel_saving: Card; subsidy: Card; payback: Card; co2: Card };
  status: {
    level: "ok" | "urgent" | "over" | "closed" | "unknown";
    advice: string;
    figures: string;
    has_notice: boolean;
    notice: string | null;
    contact: string | null;
  };
  evidence: { vehicle_cost: Row[]; running_cost: Row[]; result: Row[]; spec: string };
  /** 판정에 쓰인 주행거리 입력 방식 */
  driving: { mode: DistanceMode; annual_km: number };
  base_date: string;
}

export interface Explanation {
  headline: string;
  reason: string;
  caution: string;
  fallback: boolean;
}

export interface ModelPrice {
  base_price_manwon: number;
  trim: string;
  tax_included: boolean | null;
  price_basis: string | null;
  source: string;
  checked_at: string;
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
  modelPrices: () => request<Record<string, ModelPrice>>("/model-prices"),
  contact: (region: string) =>
    request<{ contact: string | null }>(`/contact?region=${encodeURIComponent(region)}`),
  /** region이 null이면 공통 규정만으로 답한다. */
  chat: (region: string | null, question: string, history: ChatMessage[]) =>
    request<{ answer: string }>("/chat", { region, question, history }),
};
