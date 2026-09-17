import type { DistanceMode, Grade, HoldYears, LongTrip, Profile, WorkCharger } from "./api";

export const DEFAULTS: Profile = {
  distance_mode: "annual",
  annual_km: 15000,
  commute_km: 40,
  long_trip: "거의없음",
  region: null,
  home_charger: true,
  work_charger: "없음",
  hold_years: 7,
  model: null,
  current_efficiency: 11.2,
  ev_price_manwon: 5200,
  ice_price_manwon: 3600,
  has_scrap: true,
};

export const EXAMPLE_PROFILE: Profile = {
  ...DEFAULTS,
  distance_mode: "annual",
  long_trip: "월3회이상",
  region: "성남시",
  model: "더 뉴 아이오닉5 2WD 롱레인지 19인치",
};

export const LONG_TRIP_OPTIONS: LongTrip[] = ["거의없음", "월1~2회", "월3회이상"];

export const DISTANCE_MODE_OPTIONS: { value: DistanceMode; label: string }[] = [
  { value: "annual", label: "연간 주행거리를 알고 있어요" },
  { value: "commute", label: "출퇴근 거리로 계산할게요" },
];

/**
 * 출퇴근 기반 연간 주행거리 미리보기.
 * 실제 판정은 서버(calc.calc_annual_km_from_commute, config.py 상수)가 계산하며, 여기 값은 표시용 복사본이다.
 */
const COMMUTE_DAYS_PER_YEAR = 5 * 52;
const WEEKEND_KM_BY_LONG_TRIP: Record<LongTrip, number> = {
  거의없음: 2000,
  "월1~2회": 4000,
  월3회이상: 7000,
};

export function estimateAnnualKm(commuteKm: number, longTrip: LongTrip) {
  const commute = commuteKm * COMMUTE_DAYS_PER_YEAR;
  const weekend = WEEKEND_KM_BY_LONG_TRIP[longTrip];
  return { commute, weekend, annual: commute + weekend };
}
export const WORK_CHARGER_OPTIONS: WorkCharger[] = ["있음", "없음", "해당없음"];
export const HOLD_OPTIONS: { value: HoldYears; label: string }[] = [
  { value: 3, label: "3년" },
  { value: 5, label: "5년" },
  { value: 7, label: "7년 이상" },
];

export const STEPS = ["주행", "거주", "차량·보유"];

export const GRADE_LABELS: Record<Grade, string> = {
  GREEN: "추천",
  YELLOW: "조건부",
  RED: "비추천",
};

export const GRADE_MESSAGES: Record<Grade, string> = {
  GREEN: "전환을 권장합니다",
  YELLOW: "조건을 확인한 뒤 결정하세요",
  RED: "지금은 권장하지 않습니다",
};

/** 각 단계의 필수값 */
export function stepReady(step: number, p: Profile): boolean {
  if (step === 1) return p.distance_mode === "annual" ? p.annual_km > 0 : p.commute_km >= 0;
  if (step === 2) return Boolean(p.region);
  return Boolean(p.region && p.model && p.current_efficiency > 0);
}
