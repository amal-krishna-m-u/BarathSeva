"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import ComplaintMap from "@/components/ComplaintMap";
import { EvidenceDetails } from "@/components/EvidencePanel";
import {
  acknowledgeComplaint,
  departmentResolve,
  getDepartmentComplaint,
  mediaUrl,
  reassignComplaint,
} from "@/lib/api";
import { useRequireRole } from "@/lib/auth";
import {
  AGENT_LABELS,
  CATEGORY_LABELS,
  formatDateTime,
  relativeTime,
  slaHoursRemaining,
  titleCase,
} from "@/lib/format";
import type { ComplaintDetail } from "@/lib/types";
import {
  Card,
  CardHeader,
  ErrorNote,
  Field,
  OutcomePill,
  Pill,
  PriorityPill,
  Spinner,
  StatusPill,
} from "@/components/ui";

const FIELD_OUTCOMES = [
  ["GENUINE_FIXED", "Genuine — fixed"],
  ["NOT_FOUND", "Nothing found at site"],
  ["DUPLICATE", "Duplicate of another ticket"],
  ["NOT_OUR_DEPARTMENT", "Wrong department"],
] as const;

const DEPARTMENTS = [
  ["BBMP", "BBMP — Roads & Public Works"],
  ["BWSSB", "BWSSB — Water & Sewerage"],
  ["BESCOM", "BESCOM — Electricity"],
] as const;

export default function DepartmentComplaintPage() {
  const { ready } = useRequireRole(["DEPT_ADMIN", "SUPER_ADMIN"]);
  const params = useParams<{ reference: string }>();
  const reference = decodeURIComponent(params.reference);

  const [complaint, setComplaint] = useState<ComplaintDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState<string>("GENUINE_FIXED");
  const [showReassign, setShowReassign] = useState(false);
  const [target, setTarget] = useState("BBMP");
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    if (!ready) return;
    try {
      setComplaint(await getDepartmentComplaint(reference));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [ready, reference]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(fn: () => Promise<ComplaintDetail>) {
    setBusy(true);
    try {
      setComplaint(await fn());
      setNote("");
      setReason("");
      setShowReassign(false);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!ready) return <Spinner label="Checking session" />;
  if (error && !complaint) {
    return (
      <div className="mx-auto max-w-2xl space-y-4">
        <ErrorNote>{error}</ErrorNote>
        <Link
          href="/department"
          className="text-sm text-brand-400 hover:underline"
        >
          ← Back to the department desk
        </Link>
      </div>
    );
  }
  if (!complaint) return <Spinner label="Loading complaint" />;

  const photo = mediaUrl(complaint.photo_url);
  const hours = slaHoursRemaining(complaint.sla_due_at);
  const closed =
    complaint.status === "RESOLVED" || complaint.status === "REJECTED";
  const acknowledged = complaint.status !== "DISPATCHED";

  return (
    <div className="space-y-5">
      <Link
        href="/department"
        className="inline-block text-xs text-brand-400 hover:underline"
      >
        ← Department desk
      </Link>

      <div className="flex flex-wrap items-center gap-3">
        <h1 className="font-mono text-2xl font-semibold text-white">
          {complaint.reference}
        </h1>
        <StatusPill status={complaint.status} />
        <PriorityPill priority={complaint.priority} />
        <OutcomePill
          outcome={complaint.authenticity_outcome}
          score={complaint.authenticity_score}
        />
        {complaint.sla_breached ? (
          <Pill className="bg-rose-500/15 text-rose-300 ring-rose-500/30">
            SLA breached
          </Pill>
        ) : null}
        {complaint.is_hotspot ? (
          <Pill className="bg-orange-500/15 text-orange-300 ring-orange-500/30">
            Hotspot ×{complaint.nearby_count}
          </Pill>
        ) : null}
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          <Card>
            <CardHeader
              title="The report"
              subtitle={`Filed ${relativeTime(complaint.created_at)}`}
            />
            <div className="space-y-4 p-4">
              <p className="text-sm leading-relaxed text-slate-100">
                {complaint.description}
              </p>
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Field
                  label="Category"
                  value={
                    complaint.category
                      ? (CATEGORY_LABELS[complaint.category] ??
                        complaint.category)
                      : "—"
                  }
                />
                <Field label="Ward" value={complaint.ward_name} />
                <Field
                  label="Ticket"
                  mono
                  value={complaint.external_ticket_id}
                />
                <Field
                  label="Deadline"
                  value={
                    complaint.sla_due_at
                      ? `${formatDateTime(complaint.sla_due_at)}${
                          hours !== null && !closed
                            ? ` (${Math.round(hours)}h)`
                            : ""
                        }`
                      : "—"
                  }
                />
                <Field
                  label="Reporter"
                  value={complaint.reporter_name ?? "Anonymous"}
                />
                <Field
                  label="Reporter trust"
                  value={
                    complaint.reporter_trust !== null
                      ? complaint.reporter_trust.toFixed(2)
                      : "—"
                  }
                />
                <Field
                  label="Nearby reports"
                  value={complaint.nearby_count}
                />
                <Field
                  label="Address"
                  value={complaint.address_text ?? "—"}
                />
              </dl>

              {complaint.severity_note ? (
                <p className="rounded-lg border border-ink-600 bg-ink-850/60 px-3 py-2 text-xs leading-relaxed text-mute">
                  <strong className="font-semibold text-slate-300">
                    Why this priority.
                  </strong>{" "}
                  {complaint.severity_note}
                </p>
              ) : null}
              {complaint.cluster_summary ? (
                <p className="rounded-lg border border-ink-600 bg-ink-850/60 px-3 py-2 text-xs leading-relaxed text-mute">
                  <strong className="font-semibold text-slate-300">
                    Cluster.
                  </strong>{" "}
                  {complaint.cluster_summary}
                </p>
              ) : null}
              {complaint.resolution_message ? (
                <p className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs leading-relaxed text-emerald-100">
                  <strong className="font-semibold">Sent to citizen.</strong>{" "}
                  {complaint.resolution_message}
                </p>
              ) : null}
            </div>
          </Card>

          {photo ? (
            <Card>
              <CardHeader
                title="Photographic evidence"
                subtitle="Submitted by the citizen at intake"
              />
              <div className="p-4">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={photo}
                  alt={`Evidence for ${complaint.reference}`}
                  className="max-h-96 w-full rounded-lg object-contain ring-1 ring-ink-600"
                />
              </div>
            </Card>
          ) : null}

          <Card>
            <CardHeader
              title="Evidence verdict"
              subtitle="What the deterministic checks found before this reached you"
            />
            <div className="p-4">
              {complaint.evidence ? (
                <EvidenceDetails evidence={complaint.evidence} />
              ) : (
                <p className="text-sm text-mute">No evidence record.</p>
              )}
            </div>
          </Card>
        </div>

        <div className="space-y-5">
          {!closed ? (
            <Card>
              <CardHeader
                title="Actions"
                subtitle={
                  acknowledged
                    ? "Close it when the field work is done."
                    : "Acknowledge to start the work."
                }
              />
              <div className="space-y-3 p-4">
                <textarea
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  rows={3}
                  placeholder={
                    acknowledged
                      ? "What was actually done on site?"
                      : "Optional note (crew assigned, expected visit)"
                  }
                  className="w-full resize-y rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
                />

                {!acknowledged ? (
                  <button
                    onClick={() =>
                      void act(() => acknowledgeComplaint(reference, note))
                    }
                    disabled={busy}
                    className="w-full rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-ink-950 transition hover:bg-brand-500 disabled:opacity-50"
                  >
                    {busy ? "Working…" : "Acknowledge and start work"}
                  </button>
                ) : (
                  <>
                    <select
                      value={outcome}
                      onChange={(event) => setOutcome(event.target.value)}
                      className="w-full rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-2 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
                    >
                      {FIELD_OUTCOMES.map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                    <button
                      onClick={() =>
                        void act(() =>
                          departmentResolve(reference, {
                            resolution_note: note,
                            field_outcome: outcome,
                          }),
                        )
                      }
                      disabled={busy}
                      className="w-full rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-ink-950 transition hover:bg-brand-500 disabled:opacity-50"
                    >
                      {busy ? "Working…" : "Mark resolved"}
                    </button>
                    <p className="text-[11px] leading-relaxed text-mute">
                      The field outcome is the only ground truth the system
                      gets. It adjusts the reporter&apos;s trust score.
                    </p>
                  </>
                )}

                <div className="border-t border-ink-700/70 pt-3">
                  {!showReassign ? (
                    <button
                      onClick={() => setShowReassign(true)}
                      className="w-full rounded-lg border border-ink-600 px-3.5 py-2 text-xs text-slate-300 transition hover:bg-ink-800"
                    >
                      Not our department — reassign
                    </button>
                  ) : (
                    <div className="space-y-2">
                      <select
                        value={target}
                        onChange={(event) => setTarget(event.target.value)}
                        className="w-full rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-2 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
                      >
                        {DEPARTMENTS.filter(
                          ([code]) => code !== complaint.department_code,
                        ).map(([code, label]) => (
                          <option key={code} value={code}>
                            {label}
                          </option>
                        ))}
                      </select>
                      <textarea
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                        rows={2}
                        placeholder="Why does this belong to them? (recorded in the audit trail)"
                        className="w-full resize-y rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-xs text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
                      />
                      <div className="flex gap-2">
                        <button
                          onClick={() =>
                            void act(() =>
                              reassignComplaint(reference, {
                                target_department_code: target,
                                reason,
                              }),
                            )
                          }
                          disabled={busy || reason.trim().length < 5}
                          className="flex-1 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200 transition hover:bg-amber-500/20 disabled:opacity-50"
                        >
                          Reassign
                        </button>
                        <button
                          onClick={() => setShowReassign(false)}
                          className="rounded-lg border border-ink-600 px-3 py-2 text-xs text-slate-300 transition hover:bg-ink-800"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </Card>
          ) : (
            <Card>
              <CardHeader title="Closed" />
              <div className="p-4 text-sm text-mute">
                This complaint is {titleCase(complaint.status).toLowerCase()}
                {complaint.resolved_at
                  ? ` — ${relativeTime(complaint.resolved_at)}`
                  : ""}
                .
              </div>
            </Card>
          )}

          <Card>
            <CardHeader title="Location" />
            <div className="p-4">
              <ComplaintMap
                complaints={[complaint]}
                center={[complaint.latitude, complaint.longitude]}
                zoom={16}
                height="14rem"
              />
              <p className="mt-2 font-mono text-[11px] text-mute">
                {complaint.latitude.toFixed(6)},{" "}
                {complaint.longitude.toFixed(6)}
              </p>
            </div>
          </Card>

          <Card>
            <CardHeader title="Pipeline decisions" />
            <ol className="max-h-80 divide-y divide-ink-700/70 overflow-y-auto">
              {complaint.agent_runs.map((run) => (
                <li key={run.id} className="px-4 py-2">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-xs font-medium text-slate-200">
                      {AGENT_LABELS[run.agent_name] ?? run.agent_name}
                    </span>
                    <Pill
                      className={
                        run.is_ai
                          ? "bg-violet-500/15 text-violet-300 ring-violet-500/30"
                          : "bg-sky-500/15 text-sky-300 ring-sky-500/30"
                      }
                    >
                      {run.is_ai ? "AI" : "rules"}
                    </Pill>
                    <span className="ml-auto font-mono text-[10px] text-mute">
                      {run.latency_ms ?? 0}ms
                    </span>
                  </div>
                  {run.rationale ? (
                    <p className="mt-0.5 text-[11px] leading-relaxed text-mute">
                      {run.rationale}
                    </p>
                  ) : null}
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader
          title="Audit trail"
          subtitle="Append-only and hash-chained"
        />
        <ol className="divide-y divide-ink-700/70">
          {complaint.events.map((event) => (
            <li key={event.id} className="px-4 py-2.5">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="text-sm font-medium text-slate-200">
                  {titleCase(event.event_type)}
                </span>
                <code className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-[10px] text-mute">
                  {event.actor}
                </code>
                <span className="ml-auto font-mono text-[11px] text-mute">
                  {formatDateTime(event.created_at)}
                </span>
              </div>
              {event.message ? (
                <p className="mt-0.5 text-xs leading-relaxed text-mute">
                  {event.message}
                </p>
              ) : null}
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}
