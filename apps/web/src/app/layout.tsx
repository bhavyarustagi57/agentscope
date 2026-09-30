import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "AgentScope",
  description: "Evaluate, compare, and monitor AI agents with evidence.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
