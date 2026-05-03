import { promises as fs } from "node:fs";
import path from "node:path";

export type Decision = "accept" | "reject" | "quarantine";
export type Severity = "error" | "warning" | "info";

export type Finding = {
  code: string;
  severity: Severity;
  message: string;
  je_id: string;
  line_index: number | null;
  detail: Record<string, unknown>;
};

export type Suggestion = {
  candidate_code: string;
  candidate_name: string;
  confidence: number;
  reason: string;
};

export type ProposedFix = {
  line_index: number;
  field: "debit" | "credit";
  current_amount: number;
  proposed_amount: number;
  rationale: string;
  confidence: number;
};

export type DecisionRecord = {
  je_id: string;
  description: string;
  decision: Decision;
  findings: Finding[];
  explanation: string;
  suggestions: Suggestion[];
  proposed_fix: ProposedFix | null;
};

export type Summary = {
  total: number;
  accept: number;
  reject: number;
  quarantine: number;
};

const DECISIONS_PATH = path.resolve(
  process.cwd(),
  "..",
  "prototype",
  "output",
  "decisions.json",
);

export async function loadDecisions(): Promise<DecisionRecord[]> {
  const raw = await fs.readFile(DECISIONS_PATH, "utf-8");
  return JSON.parse(raw) as DecisionRecord[];
}

export function summarize(records: DecisionRecord[]): Summary {
  const s: Summary = { total: records.length, accept: 0, reject: 0, quarantine: 0 };
  for (const r of records) s[r.decision] += 1;
  return s;
}
