"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { getComplaintStatus, mediaUrl } from "@/lib/api";
import {
  CATEGORY_LABELS,
  DEPARTMENT_LABELS,
  formatDateTime,
  relativeTime,
  slaHoursRemaining,
  titleCase,
} from "@/lib/format";
import type { ComplaintStatus } from "@/lib/types";
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

/** The transparency checklist from the problem statement, answered one by one. */
const CHECKLIST: {
  key: string;
  question: string;
  answer: (c: ComplaintStatus) => { text: string; ok: boolean | null };
}[] = [
  {
    key: "received",
    question: "Was the complaint received?",
    answer: (c) => ({ text: `Yes, ${relativeTime(c.received_at)}`, ok: true }),
  },
  {
    key: "verified",
    question: "Was it verified as a genuine civic issue?",
    answer: (c) => ({
      text: c.verified
        ? "Yes"
        : c.status === "REJECTED"
          ? "No — rejected"
          : "Awaiting review",
      ok: c.verified ? true : c.status === "REJECTED" ? false : null,
    }),
  },
  {
    key: "department",
    question: "Which department has it?",
    answer: (c) => ({
      text: c.department_code
        ? `${c.department_code} — ${DEPARTMENT_LABELS[c.department_code] ?? c.department_name ?? ""}`
        : "Not yet routed",
      ok: c.department_code ? true : null,
    }),
  },
  {
    key: "ticket",
    question: "What is the ticket number?",
    answer: (c) => ({
      text: c.external_ticket_id ?? "Not yet issued",
      ok: c.external_ticket_id ? true : null,
    }),
  },
  {
    key: "status",
    question: "What is the current status?",
    answer: (c) => ({ text: titleCase(c.status), ok: null }),
  },
  {
    key: "sla",
    question: "What is the SLA deadline?",
    answer: (c) => {
      if (!c.sla_due_at) return { text: "Not set", ok: null };
      const hours = slaHoursRemaining(c.sla_due_at);
      if (c.resolved) return { text: formatDateTime(c.sla_due_at), ok: true };
      if (hours !== null && hours < 0)
        return {
          text: `${formatDateTime(c.sla_due_at)} — breached ${Math.abs(Math.round(hours))}h ago`,
          ok: false,
        };
      return {
        text: `${formatDateTime(c.sla_due_at)} (${Math.round(hours ?? 0)}h left)`,
        ok: true,
      };
    },
  },
  {
    key: "resolved",
    question: "Has it been resolved?",
    answer: (c) => ({
      text: c.resolved ? `Yes, ${relativeTime(c.resolved_at)}` : "Not yet",
      ok: c.resolved ? true : null,
    }),
  },
];

export default function TrackDetailPage() {
  const params = useParams<{ reference: string }>();
  const reference = decodeURIComponent(params.reference);
  const [complaint, setComplaint] = useState<ComplaintStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getComplaintStatus(reference)
      .then((data) => active && setComplaint(data))
      .catch((err) => active && setError((err as Error).message));
    return () => {
      active = false;
    };
  }, [reference]);

  if (error) {
    return (
      <div className="mx-auto max-w-2xl space-y-4">
        <ErrorNote>{error}</ErrorNote>
        <Link href="/track" className="text-sm text-brand-400 hover:underline">
          ← Try another reference
        </Link>
      </div>
    );
  }
  if (!complaint) return <Spinner label="Loading complaint" />;

  const photo = mediaUrl(complaint.photo_url);

  return (
    <div className="mx-auto max-w-4xl space-y-5">
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
      </div>

      {complaint.resolution_message ? (
        <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-5 py-4">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-emerald-300">
            Resolved
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-slate-100">
            {complaint.resolution_message}
          </p>
        </div>
      ) : null}

      {complaint.rejection_reason ? (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-5 py-4">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-rose-300">
            Not accepted
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-slate-100">
            {complaint.rejection_reason}
          </p>
        </div>
      ) : null}

      {complaint.needs_human_review ? (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-5 py-4">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-amber-300">
            Held for human review
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-slate-100">
            The automated checks were not confident enough to dispatch this
            report on their own, so a reviewer will look at it. It was not
            rejected.
          </p>
        </div>
      ) : null}

      <Card>
        <CardHeader
          title="Your questions, answered"
          subtitle="Every line below is read from the stored complaint record."
        />
        <ul className="divide-y divide-ink-700/70">
          {CHECKLIST.map((item) => {
            const { text, ok } = item.answer(complaint);
            return (
              <li
                key={item.key}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5"
              >
                <span
                  className={`grid h-4 w-4 shrink-0 place-items-center rounded-full text-[10px] font-bold ${
                    ok === true
                      ? "bg-emerald-500/20 text-emerald-300"
                      : ok === false
                        ? "bg-rose-500/20 text-rose-300"
                        : "bg-ink-700 text-mute"
                  }`}
                >
                  {ok === true ? "✓" : ok === false ? "✕" : "·"}
                </span>
                <span className="text-sm text-mute">{item.question}</span>
                <span className="ml-auto text-right text-sm font-medium text-slate-200">
                  {text}
                </span>
              </li>
            );
          })}
        </ul>
      </Card>

      <div className="grid gap-5 md:grid-cols-2">
        <Card>
          <CardHeader title="Classification and location" />
          <dl className="grid grid-cols-2 gap-4 p-4">
            <Field
              label="Category"
              value={
                complaint.category
                  ? (CATEGORY_LABELS[complaint.category] ?? complaint.category)
                  : "—"
              }
            />
            <Field label="Priority" value={complaint.priority} />
            <Field label="Ward" value={complaint.ward_name} />
            <Field label="Nearby reports" value={complaint.nearby_count} />
          </dl>
          {complaint.cluster_summary ? (
            <p className="border-t border-ink-700/70 px-4 py-3 text-xs leading-relaxed text-mute">
              {complaint.cluster_summary}
            </p>
          ) : null}
        </Card>

        {photo ? (
          <Card>
            <CardHeader title="Evidence submitted" />
            <div className="p-4">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={photo}
                alt={`Evidence for ${complaint.reference}`}
                className="max-h-64 w-full rounded-lg object-contain ring-1 ring-ink-600"
              />
            </div>
          </Card>
        ) : null}
      </div>

      <Card>
        <CardHeader
          title="Audit trail"
          subtitle="Append-only and hash-chained — entries cannot be backdated or rewritten."
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
