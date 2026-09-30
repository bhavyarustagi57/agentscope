import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { TraceExplorer } from "@/components/traces/trace-explorer";

export default function TracesPage() {
  return (
    <AppShell active="Traces">
      <Suspense fallback={<div aria-busy="true" role="status" className="h-96 animate-pulse border bg-surface" aria-label="Loading Trace Explorer" />}>
        <TraceExplorer />
      </Suspense>
    </AppShell>
  );
}
