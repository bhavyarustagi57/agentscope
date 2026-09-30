import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { ExperimentRunDetail } from "@/components/experiments/experiment-run-detail";

export default async function ExperimentRunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return <AppShell active="Experiments"><Suspense fallback={<div aria-busy="true" role="status" aria-label="Loading experiment run" className="h-[36rem] animate-pulse border bg-surface" />}><ExperimentRunDetail runId={runId} /></Suspense></AppShell>;
}
