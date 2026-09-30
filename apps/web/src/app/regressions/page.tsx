import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { RegressionsWorkspace } from "@/components/regressions/regressions-workspace";

export default function RegressionsPage() {
  return <AppShell active="Regressions"><Suspense fallback={<div aria-busy="true" role="status" aria-label="Loading regressions" className="h-[36rem] animate-pulse border bg-surface" />}><RegressionsWorkspace /></Suspense></AppShell>;
}
