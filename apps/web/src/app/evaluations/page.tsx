import { AppShell } from "@/components/app-shell";
import { EvaluationsDashboard } from "@/components/evaluations/evaluations-dashboard";

export default function EvaluationsPage() {
  return <AppShell active="Evaluations"><EvaluationsDashboard /></AppShell>;
}
