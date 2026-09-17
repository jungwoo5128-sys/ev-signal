"use client";

import type { Evaluation, Explanation, Grade, Profile, Row } from "@/lib/api";
import { GRADE_LABELS, GRADE_MESSAGES } from "@/lib/profile";
import { Mono } from "./Mono";

export type Loadable<T> = { state: "loading" } | { state: "done"; data: T } | { state: "hidden" };

interface Props {
  profile: Profile;
  evaluation: Evaluation;
  explanation: Loadable<Explanation>;
  notice: Loadable<string[]>;
  regions: string[];
  regionWarning: string | null;
  onChangeRegion: (region: string) => void;
  onBack: () => void;
}

export function ResultView({
  profile,
  evaluation,
  explanation,
  notice,
  regions,
  regionWarning,
  onChangeRegion,
  onBack,
}: Props) {
  const { grade, cards, status, evidence } = evaluation;

  return (
    <div className="result">
      <div className="result-toolbar">
        <button type="button" className="btn btn-ghost" onClick={onBack}>
          ← 조건 다시 입력
        </button>
        <label className="region-switch">
          <span>지자체</span>
          <select
            className="input select"
            value={profile.region ?? ""}
            onChange={(e) => onChangeRegion(e.target.value)}
          >
            {regions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
      </div>
      {regionWarning && <p className="notice-inline">{regionWarning}</p>}

      {/* 1) 판정 */}
      <section className={`panel verdict grade-${grade}`}>
        <span className="tab-label" aria-hidden>
          판정 결과
        </span>
        <TrafficLight grade={grade} />
        <div className="verdict-body">
          <span className="eyebrow">
            {profile.region} · {profile.model}
          </span>
          {/* 등급(색)과 결론 문구는 항상 판정 규칙 결과. LLM은 설명만 담당한다. */}
          <h1 className="verdict-title">
            <Highlight text={GRADE_MESSAGES[grade]} word={GRADE_HIGHLIGHTS[grade]} grade={grade} />
          </h1>
          <VerdictReason evaluation={evaluation} explanation={explanation} />
        </div>
      </section>

      {/* 2) 핵심 수치 — 절감액과 보조금은 성격이 달라 합산하지 않는다 */}
      <section className="bento">
        <span className="tab-label" aria-hidden>
          핵심 수치
        </span>
        <MetricCard tone="green" title="연간 연료비 절감" card={cards.fuel_saving} />
        <MetricCard tone="pink" title="예상 보조금" card={cards.subsidy} />
        <MetricCard tone="peach" title="회수 기간" card={cards.payback} />
        <MetricCard tone="sky" title="연간 CO2 감축량" card={cards.co2} />
        <div className={`panel status level-${status.level}`}>
          <div className="status-head">
            <span className="card-title">보조금 접수 현황</span>
            <span className="badge">{STATUS_LABELS[status.level]}</span>
          </div>
          <p className="status-figures">
            <Mono text={status.figures} />
          </p>
          <p className="status-advice">{status.advice}</p>
        </div>
      </section>

      {/* 3) 공지 요약 */}
      <NoticeSection region={profile.region ?? ""} notice={notice} raw={status.notice} />

      {/* 4) 근거 */}
      <details className="panel evidence">
        <summary>근거 자세히 보기</summary>
        <div className="evidence-grid">
          <EvidenceTable title="차량 비용" rows={evidence.vehicle_cost} />
          <div>
            <EvidenceTable title="연간 운행 비용" rows={evidence.running_cost} />
            <EvidenceTable title="결과" rows={evidence.result} />
          </div>
        </div>
        <p className="evidence-spec">
          <strong>모델 제원</strong> · <Mono text={evidence.spec} />
        </p>
        <p className="evidence-contact">
          본 결과는 참고용입니다. 최종 확인은 관할 지자체(<Mono text={status.contact ?? "담당 부서"} />) 문의
        </p>
      </details>
    </div>
  );
}

/** 결론 문구에서 형광펜으로 강조할 부분 */
const GRADE_HIGHLIGHTS: Record<Grade, string> = {
  GREEN: "권장",
  YELLOW: "확인",
  RED: "권장하지 않습니다",
};

function Highlight({ text, word, grade }: { text: string; word: string; grade: Grade }) {
  const i = text.indexOf(word);
  if (i < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, i)}
      <mark className={`hl hl-${grade}`}>{word}</mark>
      {text.slice(i + word.length)}
    </>
  );
}

const STATUS_LABELS: Record<Evaluation["status"]["level"], string> = {
  ok: "여유",
  urgent: "소진 임박",
  over: "초과",
  closed: "접수 불가",
  unknown: "정보 없음",
};

function TrafficLight({ grade }: { grade: Grade }) {
  return (
    <div className="traffic" role="img" aria-label={`판정: ${GRADE_LABELS[grade]}`}>
      {(["GREEN", "YELLOW", "RED"] as Grade[]).map((g) => (
        <div key={g} className={`lamp lamp-${g} ${g === grade ? "is-on" : ""}`}>
          <span className="lamp-dot" />
          <span className="lamp-label">{GRADE_LABELS[g]}</span>
        </div>
      ))}
    </div>
  );
}

function VerdictReason({
  evaluation,
  explanation,
}: {
  evaluation: Evaluation;
  explanation: Loadable<Explanation>;
}) {
  if (explanation.state === "loading") {
    return (
      <div className="skeleton-lines" aria-label="판정 근거를 작성하는 중">
        <span />
        <span />
        <span className="short" />
      </div>
    );
  }
  if (explanation.state === "done" && !explanation.data.fallback) {
    return (
      <>
        <p className="verdict-reason">
          <Mono text={explanation.data.reason} />
        </p>
        {explanation.data.caution && (
          <p className="caution">
            <Mono text={explanation.data.caution} />
          </p>
        )}
      </>
    );
  }
  // 폴백: 규칙 사유 목록 그대로
  const items = evaluation.reasons.length
    ? evaluation.reasons
    : [{ severity: "warn" as const, text: "특별한 제약 사항이 없습니다" }];
  return (
    <ul className="reason-list">
      {items.map((r) => (
        <li key={r.text} className={evaluation.reasons.length ? `sev-${r.severity}` : ""}>
          <Mono text={r.text} />
        </li>
      ))}
    </ul>
  );
}

function MetricCard({
  tone,
  title,
  card,
}: {
  tone: "green" | "pink" | "peach" | "sky";
  title: string;
  card: { value: string; note: string };
}) {
  return (
    <div className={`metric tone-${tone}`}>
      <span className="card-title">{title}</span>
      <span className="metric-value num">{card.value}</span>
      <span className="metric-note">
        <Mono text={card.note} />
      </span>
    </div>
  );
}

function NoticeSection({
  region,
  notice,
  raw,
}: {
  region: string;
  notice: Loadable<string[]>;
  raw: string | null;
}) {
  if (notice.state === "hidden" || !raw) return null;
  return (
    <section className="panel notice">
      <h2 className="section-title">{region} 공지사항 요약</h2>
      {notice.state === "loading" ? (
        <div className="skeleton-lines" aria-label="공지사항을 요약하는 중">
          <span />
          <span className="short" />
        </div>
      ) : notice.data.length ? (
        <ul className="notice-list">
          {notice.data.map((item) => (
            <li key={item}>
              <Mono text={item} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">구매 결정에 영향을 주는 공지 내용이 없습니다.</p>
      )}
      <details className="notice-raw">
        <summary>원문 보기</summary>
        <div className="notice-raw-body">{raw}</div>
      </details>
    </section>
  );
}

function EvidenceTable({ title, rows }: { title: string; rows: Row[] }) {
  return (
    <div className="evidence-block">
      <span className="chip">{title}</span>
      <table className="evidence-table">
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <th scope="row">
                {r.label}
                {r.note && <small>{r.note}</small>}
              </th>
              <td className="num">{r.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
