import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { MonitoringWorkspace } from "@/components/monitoring/monitoring-workspace";

export default function MonitoringPage() {
  return <AppShell active="Monitoring"><Suspense fallback={<div aria-busy="true" role="status" aria-label="Loading monitoring" className="h-[34rem] animate-pulse border bg-surface" />}><MonitoringWorkspace /></Suspense></AppShell>;
}
