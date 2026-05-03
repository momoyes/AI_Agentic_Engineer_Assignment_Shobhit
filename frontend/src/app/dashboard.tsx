"use client";

import { useMemo, useState } from "react";
import type { Decision, DecisionRecord, Summary } from "../lib/decisions";

type FilterKey = "all" | Decision;

const DECISION_STYLES: Record<Decision, { label: string; pill: string }> = {
  accept: {
    label: "Accept",
    pill: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200",
  },
  quarantine: {
    label: "Quarantine",
    pill: "bg-amber-50 text-amber-700 ring-1 ring-amber-200",
  },
  reject: {
    label: "Reject",
    pill: "bg-rose-50 text-rose-700 ring-1 ring-rose-200",
  },
};

export default function Dashboard({
  records,
  summary,
}: {
  records: DecisionRecord[];
  summary: Summary;
}) {
  const [filter, setFilter] = useState<FilterKey>("all");
  const [openId, setOpenId] = useState<string | null>(null);

  const filtered = useMemo(
    () => (filter === "all" ? records : records.filter((r) => r.decision === filter)),
    [records, filter],
  );

  return (
    <div className="space-y-6">
      <SummaryCards summary={summary} filter={filter} setFilter={setFilter} />

      <div className="rounded-lg border border-zinc-200 bg-white shadow-sm">
        <div className="border-b border-zinc-200 px-6 py-4">
          <h2 className="text-sm font-semibold text-zinc-900">
            Journal entries
            <span className="ml-2 font-normal text-zinc-500">
              ({filtered.length} of {summary.total})
            </span>
          </h2>
        </div>

        <ul className="divide-y divide-zinc-200">
          {filtered.map((r) => (
            <EntryRow
              key={r.je_id}
              record={r}
              open={openId === r.je_id}
              onToggle={() => setOpenId(openId === r.je_id ? null : r.je_id)}
            />
          ))}
          {filtered.length === 0 && (
            <li className="px-6 py-8 text-center text-sm text-zinc-500">
              No entries match this filter.
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}

function SummaryCards({
  summary,
  filter,
  setFilter,
}: {
  summary: Summary;
  filter: FilterKey;
  setFilter: (f: FilterKey) => void;
}) {
  const cards: { key: FilterKey; label: string; count: number; accent: string }[] = [
    { key: "all", label: "Total entries", count: summary.total, accent: "text-zinc-900" },
    { key: "accept", label: "Accepted", count: summary.accept, accent: "text-emerald-700" },
    { key: "quarantine", label: "Quarantined", count: summary.quarantine, accent: "text-amber-700" },
    { key: "reject", label: "Rejected", count: summary.reject, accent: "text-rose-700" },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {cards.map((c) => {
        const active = filter === c.key;
        return (
          <button
            key={c.key}
            type="button"
            onClick={() => setFilter(c.key)}
            className={`rounded-lg border bg-white px-4 py-3 text-left shadow-sm transition ${
              active
                ? "border-zinc-900 ring-1 ring-zinc-900"
                : "border-zinc-200 hover:border-zinc-300"
            }`}
          >
            <div className="text-xs font-medium uppercase tracking-wide text-zinc-500">
              {c.label}
            </div>
            <div className={`mt-1 text-2xl font-semibold ${c.accent}`}>{c.count}</div>
          </button>
        );
      })}
    </div>
  );
}

function EntryRow({
  record,
  open,
  onToggle,
}: {
  record: DecisionRecord;
  open: boolean;
  onToggle: () => void;
}) {
  const style = DECISION_STYLES[record.decision];
  const primaryFinding = record.findings[0];

  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-start justify-between gap-4 px-6 py-4 text-left hover:bg-zinc-50"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3">
            <span className="font-mono text-sm font-medium text-zinc-900">
              {record.je_id}
            </span>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${style.pill}`}>
              {style.label}
            </span>
          </div>
          <p className="mt-1 text-sm text-zinc-700">{record.description}</p>
          {primaryFinding && (
            <p className="mt-1 truncate text-xs text-zinc-500">
              {primaryFinding.code}: {primaryFinding.message}
            </p>
          )}
        </div>
        <span className="mt-1 text-xs text-zinc-400">{open ? "▾" : "▸"}</span>
      </button>

      {open && <EntryDetail record={record} />}
    </li>
  );
}

function EntryDetail({ record }: { record: DecisionRecord }) {
  return (
    <div className="space-y-4 border-t border-zinc-100 bg-zinc-50/60 px-6 py-5">
      <Section title="Plain-English explanation">
        <p className="text-sm leading-relaxed text-zinc-700">{record.explanation}</p>
      </Section>

      {record.findings.length > 0 && (
        <Section title="Findings">
          <ul className="space-y-2">
            {record.findings.map((f, i) => (
              <li
                key={`${f.code}-${i}`}
                className="rounded-md border border-zinc-200 bg-white px-3 py-2"
              >
                <div className="flex items-center gap-2 text-xs">
                  <span className="font-mono font-semibold text-zinc-900">{f.code}</span>
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ${
                      f.severity === "error"
                        ? "bg-rose-100 text-rose-700"
                        : f.severity === "warning"
                          ? "bg-amber-100 text-amber-700"
                          : "bg-zinc-100 text-zinc-600"
                    }`}
                  >
                    {f.severity}
                  </span>
                  {f.line_index !== null && (
                    <span className="text-zinc-500">line {f.line_index}</span>
                  )}
                </div>
                <p className="mt-1 text-sm text-zinc-700">{f.message}</p>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {record.proposed_fix && (
        <Section title="Proposed fix">
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm">
            <div className="flex items-center justify-between text-zinc-900">
              <span>
                Adjust line{" "}
                <span className="font-mono font-medium">
                  {record.proposed_fix.line_index}
                </span>{" "}
                <span className="font-medium">{record.proposed_fix.field}</span>{" "}
                from{" "}
                <span className="font-mono">
                  {record.proposed_fix.current_amount.toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </span>{" "}
                →{" "}
                <span className="font-mono font-semibold">
                  {record.proposed_fix.proposed_amount.toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </span>
              </span>
              <span className="ml-3 text-xs font-medium text-amber-800">
                {Math.round(record.proposed_fix.confidence * 100)}% confidence
              </span>
            </div>
            <p className="mt-1 text-xs text-zinc-600">
              {record.proposed_fix.rationale}
            </p>
            <p className="mt-1 text-[11px] uppercase tracking-wide text-amber-800">
              Requires human confirmation before posting
            </p>
          </div>
        </Section>
      )}

      {record.suggestions.length > 0 && (
        <Section title="Mapping suggestions">
          <ul className="space-y-1.5">
            {record.suggestions.map((s) => (
              <li
                key={s.candidate_code}
                className="flex items-center justify-between rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm"
              >
                <div>
                  <span className="font-mono font-medium text-zinc-900">
                    {s.candidate_code}
                  </span>
                  <span className="ml-2 text-zinc-700">{s.candidate_name}</span>
                  <span className="ml-2 text-xs text-zinc-500">— {s.reason}</span>
                </div>
                <span className="ml-3 text-xs font-medium text-zinc-700">
                  {Math.round(s.confidence * 100)}%
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h3>
      {children}
    </div>
  );
}
