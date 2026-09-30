import { AppShell } from "@/components/app-shell";
import { ExperimentDetail } from "@/components/experiments/experiment-detail";

export default async function ExperimentPage({ params }: { params: Promise<{ experimentId: string }> }) {
  const { experimentId } = await params;
  return <AppShell active="Experiments"><ExperimentDetail experimentId={experimentId} /></AppShell>;
}
