import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { EvaluationRunDetail } from "@/components/evaluations/run-detail";

export default async function EvaluationRunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return (
    <AppShell active="Evaluations">
      <Suspense fallback={<div aria-busy="true" role="status" className="h-[32rem] animate-pulse border bg-surface" aria-label="Loading evaluation run" />}>
        <EvaluationRunDetail runId={runId} />
      </Suspense>
    </AppShell>
  );
}
