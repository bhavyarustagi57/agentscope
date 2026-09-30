export type ProductState = { state: "ready" | "empty" | "error" };

const ACRONYMS = new Map([["llm", "LLM"], ["api", "API"], ["git", "Git"]]);

export function humanizeStatus(status: string): string {
  const words = status.split("_").map((word) => ACRONYMS.get(word) ?? word);
  const label = words.join(" ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function statusTone(status: string): "active" | "attention" | "neutral" {
  const value = status.toLowerCase().replaceAll(" ", "_");
  if (["pending", "queued", "running", "open"].includes(value)) return "active";
  if (["error", "failed", "unavailable", "regression_detected", "drift_detected"].includes(value)) return "attention";
  return "neutral";
}

export function shortId(value: string): string {
  return value.length > 13 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

export function overviewAvailability(items: readonly ProductState[]): { available: number; total: number } {
  return { available: items.filter((item) => item.state !== "error").length, total: items.length };
}
