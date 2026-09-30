import { AppShell } from "@/components/app-shell";
import { IncidentDetail } from "@/components/monitoring/incident-detail";

export default async function IncidentPage({ params }: { params: Promise<{ incidentId: string }> }) {
  const { incidentId } = await params;
  return <AppShell active="Monitoring"><IncidentDetail incidentId={incidentId} /></AppShell>;
}
