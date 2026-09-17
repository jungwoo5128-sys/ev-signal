import { useId } from "react";

interface FieldProps {
  label: string;
  help?: string;
  children: React.ReactNode;
  htmlFor?: string;
}

export function Field({ label, help, children, htmlFor }: FieldProps) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {help && <p className="field-help">{help}</p>}
    </div>
  );
}

export function NumberField({
  label,
  help,
  value,
  onChange,
  step = 1,
  unit,
  decimal = false,
}: {
  label: string;
  help?: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  unit?: string;
  decimal?: boolean;
}) {
  const id = useId();
  return (
    <Field label={label} help={help} htmlFor={id}>
      <div className="input-wrap">
        <input
          id={id}
          className="input num"
          type="number"
          inputMode={decimal ? "decimal" : "numeric"}
          min={0}
          step={step}
          value={Number.isNaN(value) ? "" : value}
          onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        />
        {unit && <span className="input-unit">{unit}</span>}
      </div>
    </Field>
  );
}

export function SelectField({
  label,
  value,
  options,
  onChange,
  placeholder,
  disabled,
}: {
  label: string;
  value: string | null;
  options: string[];
  onChange: (v: string) => void;
  placeholder: string;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <Field label={label} htmlFor={id}>
      <select
        id={id}
        className="input select"
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="" disabled>
          {placeholder}
        </option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </Field>
  );
}

/** 알약형 단일 선택 */
export function Segmented<T extends string | number | boolean>({
  label,
  help,
  value,
  options,
  onChange,
}: {
  label: string;
  help?: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="field" role="radiogroup" aria-label={label}>
      <span className="field-label">{label}</span>
      <div className="segmented">
        {options.map((o) => (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={o.value === value}
            className={`segment ${o.value === value ? "is-on" : ""}`}
            onClick={() => onChange(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
      {help && <p className="field-help">{help}</p>}
    </div>
  );
}

export function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track" aria-hidden />
      <span>{label}</span>
    </label>
  );
}
