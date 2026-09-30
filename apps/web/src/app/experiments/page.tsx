import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { ExperimentsWorkspace } from "@/components/experiments/experiments-workspace";

export default function ExperimentsPage() {
  return <AppShell active="Experiments"><Suspense fallback={<div aria-busy="true" role="status" aria-label="Loading experiments" className="h-[34rem] animate-pulse border bg-surface" />}><ExperimentsWorkspace /></Suspense></AppShell>;
}
