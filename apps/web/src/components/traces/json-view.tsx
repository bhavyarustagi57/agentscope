import type { JsonValue } from "@/lib/trace-api";

export function JsonView({ value, label }: { value: JsonValue; label: string }) {
  return (
    <pre
      aria-label={label}
      className="max-h-80 max-w-full overflow-auto whitespace-pre-wrap break-words border bg-slate-950 p-3 font-mono text-xs leading-5 text-slate-100"
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
