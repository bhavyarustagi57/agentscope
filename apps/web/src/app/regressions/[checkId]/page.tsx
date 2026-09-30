import { AppShell } from "@/components/app-shell";
import { RegressionCheckDetail } from "@/components/regressions/regression-check-detail";

export default async function RegressionCheckPage({ params }: { params: Promise<{ checkId: string }> }) {
  const { checkId } = await params;
  return <AppShell active="Regressions"><RegressionCheckDetail checkId={checkId} /></AppShell>;
}
