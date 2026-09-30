import { AppShell } from "@/components/app-shell";
import { MonitorDetail } from "@/components/monitoring/monitor-detail";

export default async function MonitorPage({ params }: { params: Promise<{ monitorId: string }> }) {
  const { monitorId } = await params;
  return <AppShell active="Monitoring"><MonitorDetail monitorId={monitorId} /></AppShell>;
}
