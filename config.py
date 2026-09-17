"""상수 정의."""

GASOLINE_PRICE = 1850  # 원/L
CHARGING_PRICE = 320  # 원/kWh
CO2_GASOLINE = 2.31  # kgCO2/L
CO2_ELECTRIC = 0.4594  # kgCO2/kWh
PINE_ABSORPTION = 6.6  # kg/그루·년
# 출퇴근 기반 연간 주행거리 환산 (가정값): 출퇴근 왕복 × 주 5일 × 52주 + 주말·기타 주행
COMMUTE_DAYS_PER_WEEK = 5
WEEKS_PER_YEAR = 52
WEEKEND_KM_BY_LONG_TRIP = {  # 장거리 주행 빈도별 주말·기타 주행 (km/년)
    "거의없음": 2000,
    "월1~2회": 4000,
    "월3회이상": 7000,
}
LLM_TIMEOUT = 5  # 초, 판정 설명 (화면 표시를 직접 막음)
NOTICE_LLM_TIMEOUT = 12  # 초, 공지 요약 (판정 결과를 그린 뒤 채움)
CHAT_LLM_TIMEOUT = 15  # 초, 보조금 자격 문의 챗봇 (질문했을 때만 호출)
