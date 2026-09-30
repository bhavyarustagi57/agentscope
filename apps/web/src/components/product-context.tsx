import Link from "next/link";

import { humanizeStatus, statusTone } from "@/lib/product-ui";

export function PageHeader({ eyebrow, title, description, actions }: Readonly<{ eyebrow: string; title: string; description?: string; actions?: React.ReactNode }>) {
  return <header className="border-b pb-6"><p className="font-mono text-xs font-semibold tracking-[0.16em] text-accent">{eyebrow}</p><div className="mt-3 flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{title}</h1>{description && <p className="mt-2 max-w-4xl text-sm leading-6 text-muted">{description}</p>}</div>{actions}</div></header>;
}

export function Breadcrumbs({ items }: Readonly<{ items: readonly { label: string; href?: string }[] }>) {
  return <nav aria-label="Breadcrumb" className="mb-5"><ol className="flex flex-wrap items-center gap-2 text-sm text-muted">{items.map((item, index) => <li key={`${item.label}-${index}`} className="flex items-center gap-2">{index > 0 && <span aria-hidden="true">/</span>}{item.href ? <Link href={item.href} className="font-semibold text-accent hover:underline">{item.label}</Link> : <span aria-current="page">{item.label}</span>}</li>)}</ol></nav>;
}

export function ProductStatus({ status }: Readonly<{ status: string }>) {
  const tone = statusTone(status);
  const classes = tone === "attention" ? "border-red-300 bg-red-50 text-red-800" : tone === "active" ? "border-amber-300 bg-amber-50 text-amber-900" : "border-slate-300 bg-slate-50 text-slate-700";
  return <span className={`inline-flex border px-2 py-1 text-xs font-semibold ${classes}`}>{humanizeStatus(status)}</span>;
}
