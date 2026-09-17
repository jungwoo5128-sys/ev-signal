"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ChatWidget } from "@/components/ChatWidget";
import { InputView } from "@/components/InputView";
import { ResultView, type Loadable } from "@/components/ResultView";
import { api, type Evaluation, type Explanation, type ModelPrice, type Profile } from "@/lib/api";
import { DEFAULTS, EXAMPLE_PROFILE } from "@/lib/profile";

type Result =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "done"; evaluation: Evaluation };

export function EvSignalApp() {
  const [view, setView] = useState<"input" | "result">("input");
  const [step, setStep] = useState(1);
  const [profile, setProfileState] = useState<Profile>(DEFAULTS);
  const [regions, setRegions] = useState<string[]>([]);
  const [baseDate, setBaseDate] = useState<string | null>(null);

  const [result, setResult] = useState<Result>({ state: "idle" });
  const [explanation, setExplanation] = useState<Loadable<Explanation>>({ state: "loading" });
  const [notice, setNotice] = useState<Loadable<string[]>>({ state: "loading" });
  const [regionWarning, setRegionWarning] = useState<string | null>(null);
  const requestId = useRef(0);

  // 모델별 기본 가격. 사용자가 전기차 가격을 직접 고친 뒤에는 모델을 바꿔도 덮어쓰지 않는다.
  const [modelPrices, setModelPrices] = useState<Record<string, ModelPrice>>({});
  const [evPriceEdited, setEvPriceEdited] = useState(false);

  useEffect(() => {
    api.regions().then(setRegions).catch(() => setRegions([]));
    api.meta().then((m) => setBaseDate(m.base_date)).catch(() => {});
    api.modelPrices().then(setModelPrices).catch(() => setModelPrices({}));
  }, []);

  const setProfile = (update: Partial<Profile>) => setProfileState((p) => ({ ...p, ...update }));

  /** 판정 → (설명, 공지 요약) 순서. 느린 LLM 호출이 판정 표시를 막지 않게 나눠서 요청한다. */
  const run = useCallback(async (p: Profile) => {
    const id = ++requestId.current;
    const current = () => id === requestId.current; // 지자체를 빠르게 바꿀 때 늦게 온 응답 무시
    setView("result");
    setResult({ state: "loading" });
    setExplanation({ state: "loading" });
    setNotice({ state: "loading" });
    window.scrollTo({ top: 0, behavior: "smooth" });

    let evaluation: Evaluation;
    try {
      evaluation = await api.evaluate(p);
    } catch (e) {
      if (current()) setResult({ state: "error", message: e instanceof Error ? e.message : "오류" });
      return;
    }
    if (!current()) return;
    setResult({ state: "done", evaluation });

    api
      .explain(p)
      .then((data) => current() && setExplanation({ state: "done", data }))
      .catch(() => current() && setExplanation({ state: "hidden" }));

    if (!evaluation.status.has_notice) {
      setNotice({ state: "hidden" });
      return;
    }
    api
      .noticeSummary(p)
      .then(({ items }) => current() && setNotice(items ? { state: "done", data: items } : { state: "hidden" }))
      .catch(() => current() && setNotice({ state: "hidden" }));
  }, []);

  const submit = () => {
    setRegionWarning(null);
    run(profile);
  };

  /** 모델 선택 시 공식 가격 자동 입력 (데이터 없으면 비움). 직접 수정한 값은 유지 */
  const selectModel = (model: string) => {
    setProfileState((p) => ({
      ...p,
      model,
      ...(evPriceEdited ? {} : { ev_price_manwon: modelPrices[model]?.base_price_manwon ?? null }),
    }));
  };

  const editEvPrice = (price: number | null) => {
    setEvPriceEdited(true);
    setProfile({ ev_price_manwon: price });
  };

  /** 예시 프로필은 자동 입력 없이 고정값(전기차 5,200만원)을 그대로 쓴다 */
  const fillExample = () => {
    setProfileState(EXAMPLE_PROFILE);
    setEvPriceEdited(false);
    setStep(1);
    setRegionWarning(null);
    run(EXAMPLE_PROFILE);
  };

  const backToInput = () => {
    requestId.current++;
    setView("input");
    setStep(1);
    setResult({ state: "idle" });
    setRegionWarning(null);
  };

  /** 결과 화면에서 지자체만 바꿔 재판정. 모델 미지원 지역이면 기존 결과 유지. */
  const changeRegion = async (region: string) => {
    if (region === profile.region) return;
    const models = await api.models(region).catch(() => [] as string[]);
    if (profile.model && models.includes(profile.model)) {
      const next = { ...profile, region };
      setProfileState(next);
      setRegionWarning(null);
      run(next);
    } else {
      setRegionWarning(
        `${region}은(는) 해당 모델을 지원하지 않습니다. 아래는 ${profile.region} 기준 결과입니다.`,
      );
    }
  };

  return (
    <>
      <header className="site-header">
        <div className="container site-header-inner">
          <button type="button" className="logo" onClick={backToInput}>
            <span className="logo-mark" aria-hidden>
              <i />
              <i />
              <i />
            </span>
            전기차 신호등
          </button>
          {baseDate && (
            <span className="pill">
              데이터 기준 <span className="num">{baseDate}</span>
            </span>
          )}
        </div>
      </header>

      <main className="container main">
        {view === "input" && (
          <InputView
            profile={profile}
            setProfile={setProfile}
            step={step}
            setStep={setStep}
            regions={regions}
            onSubmit={submit}
            onExample={fillExample}
            onSelectModel={selectModel}
            onEditEvPrice={editEvPrice}
          />
        )}

        {view === "result" && result.state === "loading" && (
          <div className="panel loading-card">
            <span className="spinner" aria-hidden />
            판정하는 중...
          </div>
        )}

        {view === "result" && result.state === "error" && (
          <div className="panel error-card">
            <p>{result.message}</p>
            <button type="button" className="btn btn-primary" onClick={backToInput}>
              다시 입력하기
            </button>
          </div>
        )}

        {view === "result" && result.state === "done" && (
          <ResultView
            profile={profile}
            evaluation={result.evaluation}
            explanation={explanation}
            notice={notice}
            regions={regions}
            regionWarning={regionWarning}
            onChangeRegion={changeRegion}
            onBack={backToInput}
          />
        )}
      </main>

      {/* 모든 화면에서 표시. 지자체를 고르기 전에는 공통 규정만으로 답하고, 고르거나 바꾸면 대화를 새로 시작한다. */}
      <ChatWidget region={profile.region} />

      <footer className="site-footer">
        <div className="container">
          <p>
            데이터 출처: 무공해차 통합누리집 (기후에너지환경부)
            {baseDate && (
              <>
                {" "}· 기준 시각 <span className="num">{baseDate}</span>
              </>
            )}
          </p>
          <p>
            휘발유 1,850원/L · 충전 320원/kWh는 가정값이며, 차량 가격은 사용자 입력값입니다.
            {view === "result" && result.state === "done" && result.evaluation.driving.mode === "commute" && (
              <> 주 5일 출퇴근 및 주말·기타 주행 거리는 추정값입니다.</>
            )}{" "}
            결과는
            참고용이며 최종 보조금은 관할 지자체에 확인하세요.
          </p>
        </div>
      </footer>
    </>
  );
}
