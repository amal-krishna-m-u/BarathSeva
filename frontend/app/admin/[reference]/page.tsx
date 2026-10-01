"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import ComplaintMap from "@/components/ComplaintMap";
import { EvidenceDetails } from "@/components/EvidencePanel";
import {
  getComplaintDetail,
  mediaUrl,
  resolveComplaint,
  reviewComplaint,
} from "@/lib/api";
import {
  AGENT_LABELS,
  CATEGORY_LABELS,
  DEPARTMENT_LABELS,
  formatDateTime,
  relativeTime,
  slaHoursRemaining,
  titleCase,
} from "@/lib/format";
import type { AgentRun, ComplaintDetail } from "@/lib/types";
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

function AgentRunRow({ run }: { run: AgentRun }) {
  const failed = run.status === "FAILED";
  const skipped = run.status === "SKIPPED";
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-sm font-medium text-slate-200">
          {AGENT_LABELS[run.agent_name] ?? run.agent_name}
        </span>
        <Pill
          className={
            failed
              ? "bg-rose-500/15 text-rose-300 ring-rose-500/30"
              : skipped
                ? "bg-ink-800 text-mute ring-ink-600"
                : "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30"
          }
        >
          {run.status}
        </Pill>
        <Pill
          className={
            run.is_ai
              ? "bg-violet-500/15 text-violet-300 ring-violet-500/30"
              : "bg-sky-500/15 text-sky-300 ring-sky-500/30"
          }
          title={
            run.is_ai
              ? "Decided by a model"
              : "Decided by deterministic application logic"
          }
        >
          {run.is_ai ? "AI" : "deterministic"}
        </Pill>
        {run.provider ? (
          <code className="font-mono text-[10px] text-mute">
            {run.provider}/{run.model}
          </code>
        ) : null}
        <span className="ml-auto flex items-center gap-2 font-mono text-[11px] text-mute">
          {run.confidence !== null ? (
            <span title="Confidence">conf {run.confidence.toFixed(2)}</span>
          ) : null}
          <span title="Latency">{run.latency_ms ?? 0}ms</span>
        </span>
      </div>
      {run.rationale ? (
        <p className="mt-1 text-xs leading-relaxed text-mute">{run.rationale}</p>
      ) : null}
      {run.error ? (
        <p className="mt-1 whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-rose-300">
          {run.error.slice(0, 400)}
        </p>
      ) : null}
    </li>
  );
}

export default function AdminComplaintPage() {
  const params = useParams<{ reference: string }>();
  const reference = decodeURIComponent(params.reference);

  const [complaint, setComplaint] = useState<ComplaintDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState<string>("GENUINE_FIXED");

  const load = useCallback(async () => {
    try {
      setComplaint(await getComplaintDetail(reference));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [reference]);

  useEffect(() => {
    void load();
  }, [load]);

  async function doResolve() {
    setBusy(true);
    try {
      setComplaint(
        await resolveComplaint(reference, {
          resolution_note: note.trim(),
          field_outcome: outcome,
        }),
      );
      setNote("");
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function doReview(approve: boolean) {
    setBusy(true);
    try {
      setComplaint(
        await reviewComplaint(reference, { approve, note: note.trim() }),
      );
      setNote("");
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !complaint) {
    return (
      <div className="mx-auto max-w-2xl space-y-4">
        <ErrorNote>{error}</ErrorNote>
        <Link href="/admin" className="text-sm text-brand-400 hover:underline">
          ← Back to the command center
        </Link>
      </div>
    );
  }
  if (!complaint) return <Spinner label="Loading complaint" />;

  const photo = mediaUrl(complaint.photo_url);
  const hours = slaHoursRemaining(complaint.sla_due_at);
  const isTerminal =
    complaint.status === "RESOLVED" || complaint.status === "REJECTED";

  return (
    <div className="space-y-5">
      <Link
        href="/admin"
        className="inline-block text-xs text-brand-400 hover:underline"
      >
        ← Command center
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
        {complaint.escalation_level > 0 ? (
          <Pill className="bg-orange-500/15 text-orange-300 ring-orange-500/30">
            Escalation L{complaint.escalation_level}
          </Pill>
        ) : null}
        {complaint.is_hotspot ? (
          <Pill className="bg-orange-500/15 text-orange-300 ring-orange-500/30">
            Hotspot
          </Pill>
        ) : null}
        <Pill
          className={
            complaint.audit_chain_intact
              ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30"
              : "bg-rose-500/15 text-rose-300 ring-rose-500/30"
          }
          title="Recomputed hash chain over the audit trail"
        >
          {complaint.audit_chain_intact
            ? "Audit chain intact"
            : "AUDIT CHAIN BROKEN"}
        </Pill>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <Card>
        <CardHeader title="Report" subtitle={relativeTime(complaint.created_at)} />
        <div className="space-y-4 p-4">
          <p className="text-sm leading-relaxed text-slate-100">
            {complaint.description}
          </p>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Field
              label="Category"
              value={
                complaint.category
                  ? (CATEGORY_LABELS[complaint.category] ?? complaint.category)
                  : "—"
              }
            />
            <Field label="Ward" value={complaint.ward_name} />
            <Field
              label="Department"
              value={complaint.department_code}
              hint={
                complaint.department_code
                  ? DEPARTMENT_LABELS[complaint.department_code]
                  : undefined
              }
            />
            <Field label="Ticket" mono value={complaint.external_ticket_id} />
            <Field
              label="SLA due"
              value={
                complaint.sla_due_at
                  ? `${formatDateTime(complaint.sla_due_at)}${
                      hours !== null && !complaint.resolved_at
                        ? ` (${Math.round(hours)}h)`
                        : ""
                    }`
                  : "—"
              }
            />
            <Field label="Nearby reports" value={complaint.nearby_count} />
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
              hint="Rises with confirmed-genuine reports, falls with rejected ones"
            />
          </dl>

          {complaint.severity_note ? (
            <p className="rounded-lg border border-ink-600 bg-ink-850/60 px-3 py-2 text-xs leading-relaxed text-mute">
              <strong className="font-semibold text-slate-300">
                Priority rationale.
              </strong>{" "}
              {complaint.severity_note}
            </p>
          ) : null}
          {complaint.cluster_summary ? (
            <p className="rounded-lg border border-ink-600 bg-ink-850/60 px-3 py-2 text-xs leading-relaxed text-mute">
              <strong className="font-semibold text-slate-300">Cluster.</strong>{" "}
              {complaint.cluster_summary}
            </p>
          ) : null}
          {complaint.verification_reason ? (
            <p className="rounded-lg border border-ink-600 bg-ink-850/60 px-3 py-2 text-xs leading-relaxed text-mute">
              <strong className="font-semibold text-slate-300">
                Verifier.
              </strong>{" "}
              {complaint.verification_reason}
            </p>
          ) : null}
          {complaint.rejection_reason ? (
            <p className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs leading-relaxed text-rose-200">
              <strong className="font-semibold">Rejected.</strong>{" "}
              {complaint.rejection_reason}
            </p>
          ) : null}
          {complaint.resolution_message ? (
            <p className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs leading-relaxed text-emerald-100">
              <strong className="font-semibold">Citizen message.</strong>{" "}
              {complaint.resolution_message}
            </p>
          ) : null}
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Evidence"
            subtitle="Deterministic forensics recorded at intake"
          />
          <div className="p-4">
            {complaint.evidence ? (
              <EvidenceDetails evidence={complaint.evidence} />
            ) : (
              <p className="text-sm text-mute">No evidence record.</p>
            )}
          </div>
        </Card>

        <div className="space-y-5">
          {photo ? (
            <Card>
              <CardHeader title="Submitted photograph" />
              <div className="p-4">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={photo}
                  alt={`Evidence for ${complaint.reference}`}
                  className="max-h-72 w-full rounded-lg object-contain ring-1 ring-ink-600"
                />
              </div>
            </Card>
          ) : null}

          <Card>
            <CardHeader title="Location" />
            <div className="p-4">
              <ComplaintMap
                complaints={[complaint]}
                center={[complaint.latitude, complaint.longitude]}
                zoom={16}
                height="16rem"
              />
              <p className="mt-2 font-mono text-[11px] text-mute">
                {complaint.latitude.toFixed(6)}, {complaint.longitude.toFixed(6)}
              </p>
            </div>
          </Card>

          {!isTerminal ? (
            <Card>
              <CardHeader
                title="Officer actions"
                subtitle={
                  complaint.needs_human_review
                    ? "This complaint is held for human review."
                    : "Close the complaint once the field work is done."
                }
              />
              <div className="space-y-3 p-4">
                <textarea
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  rows={3}
                  placeholder={
                    complaint.needs_human_review
                      ? "Review note (why you are approving or rejecting)"
                      : "What was actually done on site?"
                  }
                  className="w-full resize-y rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
                />
                {complaint.needs_human_review ? (
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={() => void doReview(true)}
                      disabled={busy}
                      className="rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-ink-950 transition hover:bg-brand-500 disabled:opacity-50"
                    >
                      Approve and re-enter pipeline
                    </button>
                    <button
                      onClick={() => void doReview(false)}
                      disabled={busy}
                      className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3.5 py-2 text-sm text-rose-200 transition hover:bg-rose-500/20 disabled:opacity-50"
                    >
                      Reject
                    </button>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-2">
                    <select
                      value={outcome}
                      onChange={(event) => setOutcome(event.target.value)}
                      className="rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-2 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
                    >
                      {FIELD_OUTCOMES.map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                    <button
                      onClick={() => void doResolve()}
                      disabled={busy}
                      className="rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-ink-950 transition hover:bg-brand-500 disabled:opacity-50"
                    >
                      {busy ? "Working…" : "Mark resolved"}
                    </button>
                    <p className="w-full text-[11px] leading-relaxed text-mute">
                      The field outcome is the only ground truth the system
                      gets. It adjusts the reporter&apos;s trust score.
                    </p>
                  </div>
                )}
              </div>
            </Card>
          ) : null}
        </div>
      </div>

      <Card>
        <CardHeader
          title="Agent runs"
          subtitle="One row per node execution — input, output, confidence, latency and provider"
        />
        <ol className="divide-y divide-ink-700/70">
          {complaint.agent_runs.map((run) => (
            <AgentRunRow key={run.id} run={run} />
          ))}
          {complaint.agent_runs.length === 0 ? (
            <li className="px-4 py-6 text-center text-sm text-mute">
              No agent runs recorded.
            </li>
          ) : null}
        </ol>
      </Card>

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
                <code
                  className="font-mono text-[10px] text-mute/60"
                  title="Chain hash for this entry"
                >
                  {event.entry_hash?.slice(0, 10)}
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
