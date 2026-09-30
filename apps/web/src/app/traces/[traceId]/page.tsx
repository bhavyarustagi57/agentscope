import { AppShell } from "@/components/app-shell";
import { TraceDetail } from "@/components/traces/trace-detail";
import { buildTraceExplorerHref } from "@/lib/trace-explorer";

export default async function TraceDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ traceId: string }>;
  searchParams: Promise<{ from?: string | string[] }>;
}) {
  const [{ traceId }, query] = await Promise.all([params, searchParams]);
  return (
    <AppShell active="Traces">
      <TraceDetail traceId={traceId} explorerHref={buildTraceExplorerHref(query.from)} />
    </AppShell>
  );
}
