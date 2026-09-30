"use client";

import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/product-context";
import {
  listJudgeConfigurations,
  listJudgeRuns,
  listReferenceSets,
  listStudies,
  type CalibrationStudy,
  type JudgeConfiguration,
  type JudgeRun,
  type ReferenceSet,
} from "@/lib/calibration-api";
import { calibrationSections } from "@/lib/calibration-ui";

import { JudgeConfigurationsPanel } from "./judge-configurations-panel";
import { JudgeRunsPanel } from "./judge-runs-panel";
import { ReferenceSetsPanel } from "./reference-sets-panel";
import { StudiesPanel } from "./studies-panel";
import { ViewState, buttonClass } from "./shared";

export function CalibrationWorkspace() {
  const [referenceSets, setReferenceSets] = useState<ReferenceSet[]>([]);
  const [studies, setStudies] = useState<CalibrationStudy[]>([]);
  const [configurations, setConfigurations] = useState<JudgeConfiguration[]>([]);
  const [runs, setRuns] = useState<JudgeRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  const load = useCallback((signal: AbortSignal) => {
    Promise.all([
      listReferenceSets(signal), listStudies(signal), listJudgeConfigurations(signal), listJudgeRuns(signal),
    ]).then(([setPage, studyPage, configurationPage, runPage]) => {
      setReferenceSets(setPage.items);
      setStudies(studyPage.items);
      setConfigurations(configurationPage.items);
      setRuns(runPage.items);
      setError(null);
    }).catch((reason: unknown) => {
      if (!signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load calibration workspace.");
    }).finally(() => { if (!signal.aborted) setLoading(false); });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load, retry]);

  return (
    <div className="mx-auto max-w-[90rem]">
      <PageHeader eyebrow="EVALUATE / CALIBRATION" title="Judge calibration" description="Build an explicit human reference standard, snapshot it into a study, run an LLM judge, and inspect agreement evidence without inventing a trust score." />
        <nav aria-label="Calibration workflow" className="mt-5 overflow-x-auto border-b pb-6">
          <ol className="flex min-w-max items-center gap-2">
            {calibrationSections.map((section, index) => (
              <li key={section.id} className="flex items-center gap-2">
                <a href={`#${section.id}`} className="min-h-11 border bg-surface px-3 py-3 text-sm font-semibold outline-none hover:bg-white focus-visible:ring-2 focus-visible:ring-accent">
                  <span className="mr-2 font-mono text-xs text-muted">{index + 1}</span>{section.label}
                </a>
                {index < calibrationSections.length - 1 && <span aria-hidden="true" className="text-muted">→</span>}
              </li>
            ))}
          </ol>
        </nav>

      {loading ? (
        <div aria-busy="true" aria-label="Loading calibration workspace" className="mt-7 grid gap-5 lg:grid-cols-2">
          <div className="h-80 animate-pulse border bg-surface" /><div className="h-80 animate-pulse border bg-surface" />
        </div>
      ) : error ? (
        <div className="mt-7"><ViewState title="Couldn’t load calibration" detail={error} role="alert">
          <button className={`mt-4 ${buttonClass}`} onClick={() => { setLoading(true); setRetry((value) => value + 1); }}>Retry</button>
        </ViewState></div>
      ) : (
        <div className="mt-8 space-y-14">
          <ReferenceSetsPanel
            referenceSets={referenceSets}
            onCreated={(item) => setReferenceSets((current) => [item, ...current])}
            onUpdated={(item) => setReferenceSets((current) => current.map((value) => value.id === item.id ? item : value))}
          />
          <StudiesPanel referenceSets={referenceSets} studies={studies} onCreated={(item) => setStudies((current) => [item, ...current])} />
          <JudgeConfigurationsPanel configurations={configurations} onCreated={(item) => setConfigurations((current) => [item, ...current])} />
          <JudgeRunsPanel studies={studies} configurations={configurations} runs={runs} onCreated={(item) => setRuns((current) => [item, ...current])} />
        </div>
      )}
    </div>
  );
}
