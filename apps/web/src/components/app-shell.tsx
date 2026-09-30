import Link from "next/link";

import { NAVIGATION_GROUPS, type ActiveNavigation } from "@/lib/navigation";

export function AppShell({
  children,
  active = "Overview",
}: Readonly<{ children: React.ReactNode; active?: ActiveNavigation }>) {
  return (
    <div className="min-h-screen w-full max-w-full lg:grid lg:grid-cols-[15rem_1fr]">
      <a
        href="#main-content"
        className="fixed left-3 top-3 z-50 -translate-y-20 bg-surface px-4 py-2 font-semibold text-ink shadow-sm focus:translate-y-0 focus:outline-2 focus:outline-offset-2 focus:outline-accent"
      >
        Skip to content
      </a>

      <header className="bg-panel px-4 py-4 text-white lg:hidden">
        <Brand />
        <nav aria-label="Primary" className="mt-5">
          <NavigationGroups active={active} idPrefix="mobile-navigation" className="grid grid-cols-2 gap-x-3 gap-y-5 sm:grid-cols-4" />
        </nav>
      </header>

      <aside className="hidden bg-panel px-5 py-6 text-white lg:block lg:min-h-screen">
        <Brand />
        <nav aria-label="Primary" className="mt-10">
          <NavigationGroups active={active} idPrefix="desktop-navigation" className="space-y-6" />
        </nav>
        <p className="mt-8 border-t border-white/15 pt-4 text-xs leading-5 text-panel-muted">
          Local-first control plane
          <br />
          Evidence APIs connected
        </p>
      </aside>

      <main
        id="main-content"
        tabIndex={-1}
        className="w-full min-w-0 max-w-full px-5 py-7 sm:px-8 lg:px-12 lg:py-10"
      >
        {children}
      </main>
    </div>
  );
}

function Brand() {
  return (
    <div>
      <p className="font-mono text-xs font-semibold tracking-[0.18em] text-panel-muted">AGENT INFRASTRUCTURE</p>
      <p className="mt-1 text-xl font-semibold tracking-tight">AgentScope</p>
      <p className="mt-2 text-xs text-panel-muted">Evidence workspace</p>
    </div>
  );
}

function NavigationGroups({ active, idPrefix, className }: { active: ActiveNavigation; idPrefix: string; className: string }) {
  return (
    <div className={className}>
      {NAVIGATION_GROUPS.map((group) => {
        const headingId = `${idPrefix}-${group.label.toLowerCase()}`;
        return (
          <section key={group.label} aria-labelledby={headingId}>
            <h2 id={headingId} className="mb-2 font-mono text-[0.65rem] font-semibold uppercase tracking-[0.14em] text-panel-muted">{group.label}</h2>
            <ul className="space-y-1">
              {group.items.map((item) => (
                <li key={item.label} className="min-w-0">
                  <Link
                    href={item.href}
                    aria-current={item.label === active ? "page" : undefined}
                    className={`flex min-h-11 w-full items-center gap-3 border-l-2 px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-white ${
                      item.label === active
                        ? "border-white bg-white/10 font-semibold text-white"
                        : "border-transparent text-panel-muted hover:bg-white/5 hover:text-white"
                    }`}
                  >
                    <span className="font-mono text-xs text-panel-muted">{item.short}</span>
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
