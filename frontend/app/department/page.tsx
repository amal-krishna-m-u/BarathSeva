"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import ComplaintMap from "@/components/ComplaintMap";
import {
  getConfig,
  getDepartmentStats,
  listDepartmentComplaints,
} from "@/lib/api";
import { useRequireRole } from "@/lib/auth";
import {
  CATEGORY_LABELS,
  relativeTime,
  slaHoursRemaining,
  titleCase,
} from "@/lib/format";
import type {
  ComplaintListItem,
  DepartmentStats,
  PublicConfig,
} from "@/lib/types";
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

type Filters = {
  status: string;
  category: string;
  priority: string;
  breached: string;
  unacknowledged: string;
  search: string;
};

const EMPTY: Filters = {
  status: "",
  category: "",
  priority: "",
  breached: "",
  unacknowledged: "",
  search: "",
};

export default function DepartmentDashboard() {
  const { ready, user } = useRequireRole(["DEPT_ADMIN", "SUPER_ADMIN"]);
  const [stats, setStats] = useState<DepartmentStats | null>(null);
  const [rows, setRows] = useState<ComplaintListItem[]>([]);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!ready) return;
    try {
      const [statsData, complaints] = await Promise.all([
        getDepartmentStats(),
        listDepartmentComplaints({ ...filters, limit: "300" }),
      ]);
      setStats(statsData);
      setRows(complaints);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [ready, filters]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    getConfig().then(setConfig).catch(() => undefined);
  }, []);

  if (!ready) return <Spinner label="Checking session" />;

  const department = stats?.department ?? user?.department ?? null;

  function set<K extends keyof Filters>(key: K, value: string) {
    setFilters((previous) => ({ ...previous, [key]: value }));
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight text-white">
              {department ? department.service_label : "Department"} desk
            </h1>
            {department ? (
              <Pill className="bg-brand-500/15 text-brand-400 ring-brand-500/30">
                {department.code}
              </Pill>
            ) : null}
            {department?.is_mock ? (
              <Pill
                className="bg-amber-500/15 text-amber-300 ring-amber-500/30"
                title="Tickets are created against a mock endpoint, not a real municipal system"
              >
                mock endpoint
              </Pill>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-mute">
            {department
              ? `${department.full_name} — only complaints routed to this department are shown.`
              : "Complaints routed to your department."}
          </p>
        </div>
        <button
          onClick={() => void load()}
          className="ml-auto rounded-lg border border-ink-600 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-ink-800"
        >
          Refresh
        </button>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {stats ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Assigned" value={stats.total} />
          <Stat
            label="Unacknowledged"
            value={stats.unacknowledged}
            tone={stats.unacknowledged ? "warn" : "default"}
            hint="Dispatched to you but not yet picked up"
          />
          <Stat label="In progress" value={stats.in_progress} />
          <Stat
            label="Due within 12h"
            value={stats.due_soon}
            tone={stats.due_soon ? "warn" : "default"}
          />
          <Stat
            label="SLA breached"
            value={stats.breached}
            tone={stats.breached ? "danger" : "default"}
          />
          <Stat label="Resolved" value={stats.resolved} tone="good" />
        </div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Where the work is"
            subtitle={`${rows.length} complaint(s) in this department`}
          />
          <div className="p-4">
            <ComplaintMap complaints={rows} height="22rem" />
          </div>
        </Card>

        {stats ? (
          <div className="space-y-5">
            <Card>
              <CardHeader title="By ward" />
              <ul className="max-h-48 divide-y divide-ink-700/50 overflow-y-auto">
                {Object.entries(stats.by_ward)
                  .sort((a, b) => b[1] - a[1])
                  .map(([ward, count]) => (
                    <li
                      key={ward}
                      className="flex items-center gap-3 px-4 py-2 text-xs"
                    >
                      <span className="text-slate-300">{ward}</span>
                      <span className="ml-auto font-mono tabular-nums text-slate-200">
                        {count}
                      </span>
                    </li>
                  ))}
                {Object.keys(stats.by_ward).length === 0 ? (
                  <li className="px-4 py-4 text-center text-xs text-mute">
                    No complaints yet
                  </li>
                ) : null}
              </ul>
            </Card>
            <Card>
              <CardHeader title="By category" />
              <ul className="max-h-48 divide-y divide-ink-700/50 overflow-y-auto">
                {Object.entries(stats.by_category)
                  .sort((a, b) => b[1] - a[1])
                  .map(([category, count]) => (
                    <li
                      key={category}
                      className="flex items-center gap-3 px-4 py-2 text-xs"
                    >
                      <span className="text-slate-300">
                        {CATEGORY_LABELS[category] ?? titleCase(category)}
                      </span>
                      <span className="ml-auto font-mono tabular-nums text-slate-200">
                        {count}
                      </span>
                    </li>
                  ))}
                {Object.keys(stats.by_category).length === 0 ? (
                  <li className="px-4 py-4 text-center text-xs text-mute">
                    No complaints yet
                  </li>
                ) : null}
              </ul>
            </Card>
          </div>
        ) : null}
      </div>

      <Card>
        <CardHeader
          title="Inbox"
          subtitle="Breached first, then nearest deadline"
          action={
            Object.values(filters).some(Boolean) ? (
              <button
                onClick={() => setFilters(EMPTY)}
                className="rounded-lg border border-ink-600 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-ink-800"
              >
                Clear filters
              </button>
            ) : null
          }
        />

        <div className="flex flex-wrap gap-2 border-b border-ink-700/70 p-3">
          <input
            value={filters.search}
            onChange={(event) => set("search", event.target.value)}
            placeholder="Search description, reference or ticket"
            className="min-w-48 flex-1 rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-xs text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
          />
          <select
            value={filters.unacknowledged}
            onChange={(event) => set("unacknowledged", event.target.value)}
            className="rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
          >
            <option value="">All tickets</option>
            <option value="true">Not yet acknowledged</option>
            <option value="false">Acknowledged</option>
          </select>
          <select
            value={filters.status}
            onChange={(event) => set("status", event.target.value)}
            className="rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
          >
            <option value="">All statuses</option>
            {(config?.statuses ?? []).map((status) => (
              <option key={status} value={status}>
                {titleCase(status)}
              </option>
            ))}
          </select>
          <select
            value={filters.priority}
            onChange={(event) => set("priority", event.target.value)}
            className="rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
          >
            <option value="">All priorities</option>
            {(config?.priorities ?? []).map((priority) => (
              <option key={priority} value={priority}>
                {priority}
              </option>
            ))}
          </select>
          <select
            value={filters.category}
            onChange={(event) => set("category", event.target.value)}
            className="rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
          >
            <option value="">All categories</option>
            {(department?.categories ?? config?.categories ?? []).map((c) => (
              <option key={c} value={c}>
                {CATEGORY_LABELS[c] ?? titleCase(c)}
              </option>
            ))}
          </select>
          <select
            value={filters.breached}
            onChange={(event) => set("breached", event.target.value)}
            className="rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
          >
            <option value="">SLA: any</option>
            <option value="true">Breached only</option>
            <option value="false">Within SLA</option>
          </select>
        </div>

        {loading ? (
          <Spinner label="Loading inbox" />
        ) : rows.length === 0 ? (
          <Empty>No complaints match these filters.</Empty>
        ) : (
          <ul className="divide-y divide-ink-700/70">
            {rows.map((row) => {
              const hours = slaHoursRemaining(row.sla_due_at);
              return (
                <li key={row.id} className="px-4 py-3 hover:bg-ink-850/60">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      href={`/department/${row.reference}`}
                      className="font-mono text-sm text-brand-400 hover:underline"
                    >
                      {row.reference}
                    </Link>
                    <StatusPill status={row.status} />
                    <PriorityPill priority={row.priority} />
                    <OutcomePill
                      outcome={row.authenticity_outcome}
                      score={row.authenticity_score}
                    />
                    {row.sla_breached ? (
                      <Pill className="bg-rose-500/15 text-rose-300 ring-rose-500/30">
                        breached{hours !== null ? ` ${Math.abs(Math.round(hours))}h` : ""}
                      </Pill>
                    ) : hours !== null ? (
                      <Pill
                        className={
                          hours < 12
                            ? "bg-amber-500/15 text-amber-300 ring-amber-500/30"
                            : "bg-ink-800 text-mute ring-ink-600"
                        }
                      >
                        {Math.round(hours)}h left
                      </Pill>
                    ) : null}
                    {row.is_hotspot ? (
                      <Pill className="bg-orange-500/15 text-orange-300 ring-orange-500/30">
                        hotspot ×{row.nearby_count}
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
                      ? ` · ${row.external_ticket_id}`
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
