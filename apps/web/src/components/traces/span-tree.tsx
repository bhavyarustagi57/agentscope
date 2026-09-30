import type { SpanDetail } from "@/lib/trace-api";
import { formatDuration, type SpanTreeNode } from "@/lib/trace-explorer";

import { StatusBadge } from "./status-badge";

type VisibleNode = SpanTreeNode<SpanDetail> & { depth: number };

export function SpanTree({
  roots,
  expanded,
  selectedId,
  onToggle,
  onSelect,
}: {
  roots: SpanTreeNode<SpanDetail>[];
  expanded: Set<string>;
  selectedId: string | null;
  onToggle: (spanId: string) => void;
  onSelect: (span: SpanDetail) => void;
}) {
  const visible = flattenVisible(roots, expanded);

  return (
    <div className="overflow-hidden border bg-surface">
      <div className="grid grid-cols-[minmax(0,1fr)_auto] border-b bg-canvas px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted">
        <span>Execution hierarchy</span>
        <span>{visible.length} visible</span>
      </div>
      <ul aria-label="Span hierarchy" className="divide-y">
        {visible.map((node) => {
          const hasChildren = node.children.length > 0;
          const isExpanded = expanded.has(node.span.span_id);
          return (
            <li key={node.span.span_id} className={selectedId === node.span.span_id ? "bg-accent-soft" : "bg-surface"}>
              <div className="flex min-w-0 items-stretch" style={{ paddingInlineStart: `${Math.min(node.depth, 16) * 0.75}rem` }}>
                {hasChildren ? (
                  <button
                    type="button"
                    aria-label={`${isExpanded ? "Collapse" : "Expand"} ${node.span.name}`}
                    aria-expanded={isExpanded}
                    onClick={() => onToggle(node.span.span_id)}
                    className="min-h-12 w-11 shrink-0 font-mono text-muted outline-none hover:bg-black/5 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
                  >
                    {isExpanded ? "−" : "+"}
                  </button>
                ) : <span className="w-11 shrink-0" aria-hidden="true" />}
                <button
                  type="button"
                  aria-pressed={selectedId === node.span.span_id}
                  onClick={() => onSelect(node.span)}
                  className="grid min-w-0 flex-1 gap-2 px-2 py-2 text-left outline-none hover:bg-black/5 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent sm:grid-cols-[minmax(8rem,1fr)_auto_auto] sm:items-center"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold">{node.span.name}</span>
                    <span className="mt-0.5 block font-mono text-xs uppercase text-muted">
                      {node.span.kind}{node.malformed ? " · malformed relation" : ""}
                    </span>
                  </span>
                  <StatusBadge status={node.span.status} />
                  <span className="font-mono text-xs text-muted">{formatDuration(node.span.duration_ms)}</span>
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function flattenVisible(roots: SpanTreeNode<SpanDetail>[], expanded: Set<string>): VisibleNode[] {
  const visible: VisibleNode[] = [];
  const stack = roots.toReversed().map((node) => ({ ...node, depth: 0 }));
  while (stack.length > 0) {
    const node = stack.pop();
    if (!node) break;
    visible.push(node);
    if (expanded.has(node.span.span_id)) {
      for (const child of node.children.toReversed()) stack.push({ ...child, depth: node.depth + 1 });
    }
  }
  return visible;
}
