"use client";

import { useEffect, useState } from "react";

import { api, type Profile } from "@/lib/api";
import {
  DISTANCE_MODE_OPTIONS,
  HOLD_OPTIONS,
  LONG_TRIP_OPTIONS,
  STEPS,
  WORK_CHARGER_OPTIONS,
  estimateAnnualKm,
  stepReady,
} from "@/lib/profile";
import { NumberField, Segmented, SelectField, Toggle } from "./Fields";
import { Mono } from "./Mono";

interface Props {
  profile: Profile;
  setProfile: (update: Partial<Profile>) => void;
  step: number;
  setStep: (n: number) => void;
  regions: string[];
  onSubmit: () => void;
  onExample: () => void;
}

export function InputView({ profile, setProfile, step, setStep, regions, onSubmit, onExample }: Props) {
  return (
    <div className="input-layout">
      <section className="hero">
        <span className="eyebrow">공공데이터 기반 전기차 전환 진단</span>
        <h1 className="hero-title">
          전기차,
          <br />
          지금 <mark className="hl">바꿔도</mark> 될까?
        </h1>
        <p className="hero-sub">
          주행 거리, 충전 환경, 보유 계획을 알려주시면 지자체 보조금과 연료비 절감액을 계산해
          신호등으로 알려드립니다.
        </p>
        <ul className="hero-points">
          <li>
            <span className="point-dot tone-green" />
            지자체·모델별 보조금
          </li>
          <li>
            <span className="point-dot tone-pink" />
            연료비 절감과 회수 기간
          </li>
          <li>
            <span className="point-dot tone-sky" />
            CO2 감축량
          </li>
        </ul>
        <button type="button" className="btn btn-ghost" onClick={onExample}>
          예시 프로필로 결과 보기 →
        </button>
      </section>

      <section className="panel form-card" aria-label="조건 입력">
        <span className="tab-label" aria-hidden>
          조건 입력 <span className="num">{step}/{STEPS.length}</span>
        </span>
        <Stepper current={step} />
        <h2 className="form-title">
          <span className="num">STEP {step}</span> {STEPS[step - 1]}
        </h2>

        <div className="form-fields">
          {step === 1 && <StepDriving profile={profile} setProfile={setProfile} />}
          {step === 2 && <StepResidence profile={profile} setProfile={setProfile} regions={regions} />}
          {step === 3 && <StepVehicle profile={profile} setProfile={setProfile} />}
        </div>

        <div className="form-nav">
          {step > 1 ? (
            <button type="button" className="btn btn-outline" onClick={() => setStep(step - 1)}>
              이전
            </button>
          ) : (
            <span />
          )}
          {step < STEPS.length ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={!stepReady(step, profile)}
              onClick={() => setStep(step + 1)}
            >
              다음
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              disabled={!stepReady(step, profile)}
              onClick={onSubmit}
            >
              판정 받기
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

function Stepper({ current }: { current: number }) {
  return (
    <ol className="stepper">
      {STEPS.map((label, i) => {
        const n = i + 1;
        const state = n === current ? "is-current" : n < current ? "is-done" : "is-todo";
        return (
          <li key={label} className={`stepper-item ${state}`}>
            <span className="stepper-dot num">{n < current ? "✓" : n}</span>
            <span className="stepper-label">{label}</span>
          </li>
        );
      })}
    </ol>
  );
}

type StepProps = { profile: Profile; setProfile: (u: Partial<Profile>) => void };

function StepDriving({ profile, setProfile }: StepProps) {
  const estimate = estimateAnnualKm(profile.commute_km, profile.long_trip);
  const km = (n: number) => n.toLocaleString("ko-KR");
  return (
    <>
      <Segmented
        label="주행거리 입력 방식"
        value={profile.distance_mode}
        options={DISTANCE_MODE_OPTIONS}
        onChange={(v) => setProfile({ distance_mode: v })}
      />
      {profile.distance_mode === "annual" ? (
        <NumberField
          label="연간 주행거리"
          unit="km"
          step={1000}
          value={profile.annual_km}
          onChange={(v) => setProfile({ annual_km: v })}
        />
      ) : (
        <div>
          <NumberField
            label="출퇴근 왕복 거리"
            unit="km"
            value={profile.commute_km}
            onChange={(v) => setProfile({ commute_km: v })}
          />
          <p className="field-help distance-estimate" aria-live="polite">
            <Mono
              text={`연간 약 ${km(estimate.annual)}km로 계산됩니다 (출퇴근 ${km(estimate.commute)} + 주말·기타 ${km(estimate.weekend)})`}
            />
          </p>
        </div>
      )}
      <Segmented
        label="장거리 주행 빈도"
        value={profile.long_trip}
        options={LONG_TRIP_OPTIONS.map((v) => ({ value: v, label: v }))}
        onChange={(v) => setProfile({ long_trip: v })}
      />
    </>
  );
}

function StepResidence({ profile, setProfile, regions }: StepProps & { regions: string[] }) {
  return (
    <>
      <SelectField
        label="거주 지자체"
        value={profile.region}
        options={regions}
        placeholder={regions.length ? "지자체를 선택하세요" : "불러오는 중..."}
        onChange={(v) => setProfile({ region: v, model: null })}
      />
      <Segmented
        label="주거지 충전기"
        value={profile.home_charger}
        options={[
          { value: true, label: "있음" },
          { value: false, label: "없음" },
        ]}
        onChange={(v) => setProfile({ home_charger: v })}
      />
      <Segmented
        label="근무지 충전기"
        help="재택근무·무직 등은 '해당없음'을 선택하세요. 판정에서는 '없음'과 같습니다."
        value={profile.work_charger}
        options={WORK_CHARGER_OPTIONS.map((v) => ({ value: v, label: v }))}
        onChange={(v) => setProfile({ work_charger: v })}
      />
    </>
  );
}

function StepVehicle({ profile, setProfile }: StepProps) {
  const [models, setModels] = useState<{ region: string; list: string[] } | null>(null);
  const region = profile.region;

  useEffect(() => {
    if (!region) return;
    let alive = true;
    api
      .models(region)
      .then((list) => alive && setModels({ region, list }))
      .catch(() => alive && setModels({ region, list: [] }));
    return () => {
      alive = false;
    };
  }, [region]);

  const loaded = models?.region === region;
  const list = loaded ? models.list : [];

  return (
    <>
      <Segmented
        label="예상 보유 기간"
        value={profile.hold_years}
        options={HOLD_OPTIONS}
        onChange={(v) => setProfile({ hold_years: v })}
      />
      <SelectField
        label={region ? `관심 모델 (${region} 지원 모델)` : "관심 모델"}
        value={list.includes(profile.model ?? "") ? profile.model : null}
        options={list}
        placeholder={!loaded ? "불러오는 중..." : list.length ? "모델을 선택하세요" : "지원 모델 정보가 없습니다"}
        disabled={!list.length}
        onChange={(v) => setProfile({ model: v })}
      />
      {loaded && !list.length && <p className="notice-inline">이 지역은 지원 모델 정보가 없습니다</p>}
      <NumberField
        label="현재 차량 연비"
        unit="km/L"
        step={0.1}
        decimal
        value={profile.current_efficiency}
        onChange={(v) => setProfile({ current_efficiency: v })}
      />
      <div className="field-row">
        <NumberField
          label="관심 전기차 가격"
          help="보조금 적용 전 가격"
          unit="만원"
          step={100}
          value={profile.ev_price_manwon}
          onChange={(v) => setProfile({ ev_price_manwon: v })}
        />
        <NumberField
          label="비교 내연기관차 가격"
          help="대신 고려 중인 차량"
          unit="만원"
          step={100}
          value={profile.ice_price_manwon}
          onChange={(v) => setProfile({ ice_price_manwon: v })}
        />
      </div>
      <Toggle
        label="현재 차량 폐차 또는 매도 예정"
        checked={profile.has_scrap}
        onChange={(v) => setProfile({ has_scrap: v })}
      />
    </>
  );
}
