"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Card, CardHeader } from "@/components/ui";

export default function TrackSearchPage() {
  const router = useRouter();
  const [reference, setReference] = useState("");

  function go(event: React.FormEvent) {
    event.preventDefault();
    const cleaned = reference.trim().toUpperCase();
    if (cleaned) router.push(`/track/${encodeURIComponent(cleaned)}`);
  }

  return (
    <div className="mx-auto max-w-xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-white">
          Track a complaint
        </h1>
        <p className="mt-2 text-sm text-mute">
          Enter the reference you received. Status, the department handling it,
          the ticket number, the SLA deadline and the full audit trail are all
          answered from stored state.
        </p>
      </div>

      <Card>
        <CardHeader title="Complaint reference" />
        <form onSubmit={go} className="flex flex-wrap gap-2 p-4">
          <input
            value={reference}
            onChange={(event) => setReference(event.target.value)}
            placeholder="BRS-000001"
            className="min-w-0 flex-1 rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 font-mono text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
          />
          <button
            type="submit"
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-ink-950 transition hover:bg-brand-500"
          >
            Look up
          </button>
        </form>
      </Card>
    </div>
  );
}
