import type { MonitoringIncident } from "./monitoring-api.ts";

export type OverviewEvidence = {
  count: number;
  active?: number;
  attention?: number;
  demo?: boolean;
};

export type OverviewResult =
  | ({ state: "ready" | "empty" } & OverviewEvidence)
  | { state: "error"; message: string };

export async function settleOverviewSources(
  sources: Record<string, Promise<OverviewEvidence>>,
): Promise<Record<string, OverviewResult>> {
  const entries = Object.entries(sources);
  const settled = await Promise.allSettled(entries.map(([, promise]) => promise));
  return Object.fromEntries(settled.map((result, index) => {
    const key = entries[index][0];
    if (result.status === "rejected") return [key, { state: "error", message: "Evidence unavailable." }];
    return [key, { ...result.value, state: result.value.count === 0 ? "empty" : "ready" }];
  }));
}

export function isEmptyOverview(items: readonly OverviewResult[]): boolean {
  return items.length > 0 && items.every((item) => item.state === "empty");
}

export function hasDemoEvidence(items: readonly OverviewResult[]): boolean {
  return items.some((item) => item.state === "ready" && item.demo === true);
}

export function summarizeMonitoringIncidents(
  items: readonly Pick<MonitoringIncident, "status">[],
): OverviewEvidence {
  const active = items.filter(
    (item) => item.status === "open" || item.status === "acknowledged",
  ).length;
  return { count: active, active, attention: active };
}
