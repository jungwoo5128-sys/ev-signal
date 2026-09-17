"use client";

import Image from "next/image";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { api, type ChatMessage } from "@/lib/api";
import { Mono } from "./Mono";

const EXAMPLES = [
  "다자녀 가구는 어떤 혜택이 있나요?",
  "생애 최초 구매자 조건이 뭔가요?",
  "우선순위로 신청하려면 뭘 준비해야 하나요?",
];

/** 서버도 최근 6턴만 쓰지만, 요청 크기를 줄이려고 클라이언트에서도 자른다. */
const HISTORY_MESSAGES = 12;
const UNAVAILABLE = "일시적으로 답변할 수 없습니다.";
/** 지자체를 고르지 않았을 때 고지에 쓰는 전국 공통 문의처 */
const COMMON_CONTACT = "한국환경공단 1661-0970";

interface Props {
  /** 선택한 지자체. 없으면 공통 규정만으로 답한다. */
  region: string | null;
}

/**
 * 보조금 자격 문의 챗봇 (우하단 플로팅).
 * 판정과 무관한 정보 제공이며, 답변 근거·안전장치는 서버의 answer_eligibility()가 담당한다.
 */
export function ChatWidget({ region }: Props) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [mascotFailed, setMascotFailed] = useState(false);

  // 지자체가 바뀌면 근거(공지)가 달라지므로 대화를 초기화하고, 진행 중인 답변은 버린다.
  // (렌더 중 이전 값과 비교해 상태를 조정하는 React 권장 방식)
  const [prevRegion, setPrevRegion] = useState(region);
  const [conversation, setConversation] = useState(0);
  if (region !== prevRegion) {
    setPrevRegion(region);
    setConversation((c) => c + 1);
    setMessages([]);
    setDraft("");
    setWaiting(false);
  }

  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const conversationRef = useRef(conversation);
  const panelId = useId();

  useEffect(() => {
    conversationRef.current = conversation;
  }, [conversation]);

  // 고지에 쓸 담당부서·연락처 (지자체를 고른 경우에만 조회)
  const [contactInfo, setContactInfo] = useState<{ region: string; contact: string | null } | null>(null);
  useEffect(() => {
    if (!region) return;
    let cancelled = false;
    api
      .contact(region)
      .then(({ contact }) => !cancelled && setContactInfo({ region, contact }))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [region]);
  const contact = !region
    ? COMMON_CONTACT
    : (contactInfo?.region === region && contactInfo.contact) || "담당 부서";

  // 새 메시지·로딩 표시가 생기면 맨 아래로
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages, waiting, open]);

  useEffect(() => {
    if (open && !waiting) inputRef.current?.focus();
  }, [open, waiting]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const ask = async (question: string) => {
    const q = question.trim();
    if (!q || waiting) return;
    const id = conversation;
    const history = messages.slice(-HISTORY_MESSAGES);
    setMessages((m) => [...m, { role: "user", content: q }]);
    setDraft("");
    setWaiting(true);

    let answer: string;
    try {
      answer = (await api.chat(region, q, history)).answer;
    } catch {
      answer = UNAVAILABLE;
    }
    if (id !== conversationRef.current) return; // 그사이 지자체가 바뀜
    setMessages((m) => [...m, { role: "assistant", content: answer }]);
    setWaiting(false);
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    ask(draft);
  };

  return (
    <>
      {open && (
        <section id={panelId} className="chat-panel" role="dialog" aria-label="보조금 자격 문의">
          <header className="chat-head">
            <h2>보조금 자격 문의</h2>
            <button type="button" className="chat-close" onClick={() => setOpen(false)} aria-label="닫기">
              ×
            </button>
          </header>

          {!region && (
            <p className="chat-region-hint">지자체를 선택하면 해당 지역 공고 내용까지 안내해 드립니다</p>
          )}
          <p className="chat-guide">다자녀, 생애최초, 소상공인 등 추가 지원 자격을 물어보세요</p>

          <div className="chat-log" ref={logRef} aria-live="polite">
            {messages.length === 0 && !waiting && (
              <div className="chat-examples">
                {EXAMPLES.map((q) => (
                  <button key={q} type="button" className="chat-example" onClick={() => ask(q)}>
                    {q}
                  </button>
                ))}
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`chat-msg chat-msg-${m.role}`}>
                <FormattedAnswer text={m.content} />
              </div>
            ))}
            {waiting && (
              <div className="chat-msg chat-msg-assistant chat-typing" aria-label="답변을 작성하는 중">
                <span />
                <span />
                <span />
              </div>
            )}
          </div>

          <form className="chat-form" onSubmit={submit}>
            <input
              ref={inputRef}
              className="chat-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={waiting ? "답변을 기다리는 중..." : "질문을 입력하세요"}
              maxLength={500}
              disabled={waiting}
              aria-label="질문"
            />
            <button type="submit" className="chat-send" disabled={waiting || !draft.trim()}>
              보내기
            </button>
          </form>

          <p className="chat-disclaimer">
            본 답변은 참고용입니다. 최종 확인은 관할 지자체(<Mono text={contact} />)
          </p>
        </section>
      )}

      <button
        type="button"
        className="chat-fab"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "보조금 자격 문의 닫기" : "보조금 자격 문의 열기"}
        aria-expanded={open}
        aria-controls={panelId}
      >
        {mascotFailed ? (
          <span className="chat-fab-fallback" aria-hidden>
            ?
          </span>
        ) : (
          <Image src="/mascot.png" alt="" width={64} height={64} onError={() => setMascotFailed(true)} />
        )}
      </button>
    </>
  );
}

/** LLM 답변의 **굵게** 표시만 살리고, 숫자는 모노스페이스로. */
function FormattedAnswer({ text }: { text: string }) {
  return (
    <>
      {text.split(/\*\*(.+?)\*\*/).map((part, i) =>
        i % 2 === 1 ? (
          <strong key={i}>
            <Mono text={part} />
          </strong>
        ) : (
          <Mono key={i} text={part} />
        ),
      )}
    </>
  );
}
