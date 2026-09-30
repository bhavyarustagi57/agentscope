import { AppShell } from "@/components/app-shell";
import { BisectionSessionDetail } from "@/components/regressions/bisection-session-detail";

export default async function BisectionSessionPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  return <AppShell active="Regressions"><BisectionSessionDetail sessionId={sessionId} /></AppShell>;
}
