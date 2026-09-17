"""상수 정의."""

GASOLINE_PRICE = 1850  # 원/L
CHARGING_PRICE = 320  # 원/kWh
CO2_GASOLINE = 2.31  # kgCO2/L
CO2_ELECTRIC = 0.4594  # kgCO2/kWh
PINE_ABSORPTION = 6.6  # kg/그루·년
LLM_TIMEOUT = 5  # 초, 판정 설명 (화면 표시를 직접 막음)
NOTICE_LLM_TIMEOUT = 12  # 초, 공지 요약 (판정 결과를 그린 뒤 채움)
CHAT_LLM_TIMEOUT = 15  # 초, 보조금 자격 문의 챗봇 (질문했을 때만 호출)
