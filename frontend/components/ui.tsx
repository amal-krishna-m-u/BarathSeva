/** Small presentation primitives shared across pages. */

import Link from "next/link";
import type { ReactNode } from "react";
import {
  OUTCOME_STYLES,
  PRIORITY_STYLES,
  STATUS_STYLES,
  titleCase,
} from "@/lib/format";

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-ink-700/70 bg-ink-900/60 ${className}`}
    >
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start gap-3 border-b border-ink-700/70 px-4 py-3">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold tracking-tight text-white">
          {title}
        </h2>
        {subtitle ? (
          <p className="mt-0.5 text-xs text-mute">{subtitle}</p>
        ) : null}
      </div>
      {action ? <div className="ml-auto shrink-0">{action}</div> : null}
    </div>
  );
}

export function Pill({
  children,
  className = "",
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${className}`}
    >
      {children}
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  return (
    <Pill
      className={
        STATUS_STYLES[status] ?? "bg-ink-800 text-slate-300 ring-ink-600"
      }
    >
      {titleCase(status)}
    </Pill>
  );
}

export function PriorityPill({ priority }: { priority: string | null }) {
  if (!priority) return null;
  return (
    <Pill
      className={
        PRIORITY_STYLES[priority] ?? "bg-ink-800 text-slate-300 ring-ink-600"
      }
      title={
        priority === "P1"
          ? "Immediate danger to life or essential services"
          : priority === "P2"
            ? "Serious"
            : priority === "P3"
              ? "Routine"
              : "Minor"
      }
    >
      {priority}
    </Pill>
  );
}

export function OutcomePill({
  outcome,
  score,
}: {
  outcome: string | null;
  score?: number | null;
}) {
  if (!outcome) return null;
  return (
    <Pill
      className={
        OUTCOME_STYLES[outcome] ?? "bg-ink-800 text-slate-300 ring-ink-600"
      }
      title="Evidence authenticity outcome"
    >
      {titleCase(outcome)}
      {typeof score === "number" ? (
        <span className="font-mono tabular-nums opacity-80">
          {score.toFixed(2)}
        </span>
      ) : null}
    </Pill>
  );
}

export function Field({
  label,
  value,
  mono = false,
  hint,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
  hint?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-mute" title={hint}>
        {label}
      </dt>
      <dd
        className={`mt-0.5 truncate text-sm text-slate-200 ${mono ? "font-mono" : ""}`}
      >
        {value ?? "—"}
      </dd>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="px-4 py-8 text-center text-sm text-mute">{children}</p>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
      {children}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 px-4 py-10 text-sm text-mute">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink-600 border-t-brand-500" />
      {label}…
    </div>
  );
}

export function RefLink({ reference }: { reference: string }) {
  return (
    <Link
      href={`/admin/${reference}`}
      className="font-mono text-sm text-brand-400 underline-offset-2 hover:underline"
    >
      {reference}
    </Link>
  );
}

export function Stat({
  label,
  value,
  tone = "default",
  hint,
}: {
  label: string;
  value: ReactNode;
  tone?: "default" | "danger" | "warn" | "good";
  hint?: string;
}) {
  const tones = {
    default: "text-white",
    danger: "text-rose-300",
    warn: "text-amber-300",
    good: "text-emerald-300",
  };
  return (
    <div
      className="rounded-xl border border-ink-700/70 bg-ink-900/60 px-4 py-3"
      title={hint}
    >
      <p className="text-[11px] uppercase tracking-wide text-mute">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${tones[tone]}`}>
        {value}
      </p>
    </div>
  );
}
