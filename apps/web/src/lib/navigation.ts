export type NavigationItem = { label: string; short: string; href: string };

export const NAVIGATION_GROUPS: readonly { label: string; items: readonly NavigationItem[] }[] = [
  { label: "Workspace", items: [{ label: "Overview", short: "OV", href: "/" }] },
  { label: "Observe", items: [
    { label: "Traces", short: "TR", href: "/traces" },
    { label: "Monitoring", short: "MO", href: "/monitoring" },
  ] },
  { label: "Evaluate", items: [
    { label: "Evaluations", short: "EV", href: "/evaluations" },
    { label: "Calibration", short: "CA", href: "/calibration" },
    { label: "Experiments", short: "EX", href: "/experiments" },
  ] },
  { label: "Investigate", items: [{ label: "Regressions", short: "RG", href: "/regressions" }] },
] as const;

export const PRIMARY_NAVIGATION = NAVIGATION_GROUPS.flatMap((group) => group.items);

export type ActiveNavigation = (typeof PRIMARY_NAVIGATION)[number]["label"];
