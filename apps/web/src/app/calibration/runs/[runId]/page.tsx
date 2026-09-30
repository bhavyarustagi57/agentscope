import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { CalibrationRunDetail } from "@/components/calibration/calibration-run-detail";

export default async function CalibrationRunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return (
    <AppShell active="Calibration">
      <Suspense fallback={<div aria-busy="true" role="status" aria-label="Loading calibration judge run" className="h-[36rem] animate-pulse border bg-surface" />}>
        <CalibrationRunDetail runId={runId} />
      </Suspense>
    </AppShell>
  );
}
