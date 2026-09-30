import { AppShell } from "@/components/app-shell";
import { OverviewDashboard } from "@/components/overview-dashboard";

export default function Home() {
  return <AppShell active="Overview"><OverviewDashboard /></AppShell>;
}
