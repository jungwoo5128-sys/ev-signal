const NUMBER_RE = /(-?\d[\d,]*(?:\.\d+)?)/;

/** 문장 속 숫자(금액·수치·연도)만 모노스페이스로 표시한다. */
export function Mono({ text }: { text: string }) {
  return (
    <>
      {text.split(NUMBER_RE).map((part, i) =>
        i % 2 === 1 ? (
          <span key={i} className="num">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </>
  );
}
