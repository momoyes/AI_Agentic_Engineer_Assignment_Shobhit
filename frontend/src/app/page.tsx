import Dashboard from "./dashboard";
import { loadDecisions, summarize } from "../lib/decisions";

export const dynamic = "force-dynamic";

export default async function Home() {
  let records;
  try {
    records = await loadDecisions();
  } catch (err) {
    return <EmptyState error={err instanceof Error ? err.message : String(err)} />;
  }

  const summary = summarize(records);

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto max-w-5xl px-6 py-5">
          <h1 className="text-lg font-semibold text-zinc-900">
            Manual adjustments — validation results
          </h1>
          <p className="mt-0.5 text-sm text-zinc-500">
            Period 2024-Q4 · {summary.total} entries reviewed
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        <Dashboard records={records} summary={summary} />
      </main>
    </div>
  );
}

function EmptyState({ error }: { error: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50 px-6">
      <div className="max-w-md rounded-lg border border-zinc-200 bg-white p-6 shadow-sm">
        <h1 className="text-base font-semibold text-zinc-900">No decisions found</h1>
        <p className="mt-2 text-sm text-zinc-600">
          Could not load{" "}
          <code className="rounded bg-zinc-100 px-1 py-0.5 text-xs">
            prototype/output/decisions.json
          </code>
          . Run the pipeline first:
        </p>
        <pre className="mt-3 overflow-x-auto rounded bg-zinc-900 p-3 text-xs text-zinc-100">
          {`cd ../prototype
python3 main.py --inputs ../inputs --out output --llm`}
        </pre>
        <p className="mt-3 text-xs text-zinc-400">{error}</p>
      </div>
    </div>
  );
}
