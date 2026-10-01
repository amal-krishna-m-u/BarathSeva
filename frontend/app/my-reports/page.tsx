"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { myComplaints } from "@/lib/api";
import { useAuth, useRequireRole } from "@/lib/auth";
import {
  CATEGORY_LABELS,
  relativeTime,
  slaHoursRemaining,
} from "@/lib/format";
import type { ComplaintListItem } from "@/lib/types";
import {
  Card,
  CardHeader,
  Empty,
  ErrorNote,
  OutcomePill,
  Pill,
  PriorityPill,
  Spinner,
  Stat,
  StatusPill,
} from "@/components/ui";

export default function MyReportsPage() {
  const { ready } = useRequireRole(["CITIZEN", "DEPT_ADMIN", "SUPER_ADMIN"]);
  const { user } = useAuth();
  const [rows, setRows] = useState<ComplaintListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    myComplaints()
      .then(setRows)
      .catch((err) => setError((err as Error).message));
  }, [ready]);

  if (!ready) return <Spinner label="Checking session" />;
  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!rows) return <Spinner label="Loading your reports" />;

  const resolved = rows.filter((r) => r.status === "RESOLVED").length;
  const open = rows.filter(
    (r) => r.status !== "RESOLVED" && r.status !== "REJECTED",
  ).length;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-white">
          My reports
        </h1>
        <p className="mt-1 text-sm text-mute">
          Everything you have filed, with the department handling it and the
          deadline it is held to.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Filed" value={rows.length} />
        <Stat label="Open" value={open} />
        <Stat label="Resolved" value={resolved} tone="good" />
        <Stat
          label="Trust score"
          value={user ? user.trust_score.toFixed(2) : "—"}
          hint="Rises with confirmed-genuine reports, falls with rejected ones"
        />
      </div>

      <Card>
        <CardHeader title="Reports" subtitle={`${rows.length} total`} />
        {rows.length === 0 ? (
          <Empty>
            You have not filed any reports yet.{" "}
            <Link href="/" className="text-brand-400 hover:underline">
              Report a civic problem →
            </Link>
          </Empty>
        ) : (
          <ul className="divide-y divide-ink-700/70">
            {rows.map((row) => {
              const hours = slaHoursRemaining(row.sla_due_at);
              return (
                <li key={row.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      href={`/track/${row.reference}`}
                      className="font-mono text-sm text-brand-400 hover:underline"
                    >
                      {row.reference}
                    </Link>
                    <StatusPill status={row.status} />
                    <PriorityPill priority={row.priority} />
                    {row.department_code ? (
                      <Pill className="bg-ink-800 text-slate-300 ring-ink-600">
                        {row.department_code}
                      </Pill>
                    ) : null}
                    <OutcomePill
                      outcome={row.authenticity_outcome}
                      score={row.authenticity_score}
                    />
                    {row.sla_breached ? (
                      <Pill className="bg-rose-500/15 text-rose-300 ring-rose-500/30">
                        SLA breached
                      </Pill>
                    ) : null}
                    <span className="ml-auto text-[11px] text-mute">
                      {relativeTime(row.created_at)}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-300">
                    {row.description}
                  </p>
                  <p className="mt-1 text-[11px] text-mute">
                    {row.category
                      ? (CATEGORY_LABELS[row.category] ?? row.category)
                      : "Unclassified"}
                    {row.ward_name ? ` · ${row.ward_name}` : ""}
                    {row.external_ticket_id
                      ? ` · ticket ${row.external_ticket_id}`
                      : ""}
                    {row.sla_due_at && !row.sla_breached && hours !== null
                      ? ` · ${Math.round(hours)}h to deadline`
                      : ""}
                  </p>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
