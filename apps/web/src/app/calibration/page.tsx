import { AppShell } from "@/components/app-shell";
import { CalibrationWorkspace } from "@/components/calibration/calibration-workspace";

export default function CalibrationPage() {
  return <AppShell active="Calibration"><CalibrationWorkspace /></AppShell>;
}
