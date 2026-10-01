"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import ComplaintMap from "@/components/ComplaintMap";
import LiveFeed from "@/components/LiveFeed";
import {
  getConfig,
  getHotspots,
  getStats,
  getWards,
  listComplaints,
  runSlaSweep,
} from "@/lib/api";
import {
  CATEGORY_LABELS,
  formatDateTime,
  relativeTime,
  slaHoursRemaining,
  titleCase,
} from "@/lib/format";
import type {
  ComplaintListItem,
  Hotspot,
  PublicConfig,
  Stats,
  SweepResult,
  Ward,
} from "@/lib/types";
import { useRequireRole } from "@/lib/auth";
import {
  Card,
  CardHeader,
  Empty,
  ErrorNote,
  OutcomePill,
  Pill,
  PriorityPill,
  RefLink,
  Spinner,
  Stat,
  StatusPill,
} from "@/components/ui";

type Filters = {
  status: string;
  category: string;
  priority: string;
  department_code: string;
  ward_id: string;
  breached: string;
  needs_review: string;
  search: string;
};

const EMPTY_FILTERS: Filters = {
  status: "",
  category: "",
  priority: "",
  department_code: "",
  ward_id: "",
  breached: "",
  needs_review: "",
  search: "",
};

export default function CommandCenterPage() {
  const { ready } = useRequireRole(["SUPER_ADMIN"]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [complaints, setComplaints] = useState<ComplaintListItem[]>([]);
  const [wards, setWards] = useState<Ward[]>([]);
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sweeping, setSweeping] = useState(false);
  const [sweep, setSweep] = useState<SweepResult | null>(null);

  const load = useCallback(async () => {
    if (!ready) return;
    try {
      const [statsData, complaintData, hotspotData] = await Promise.all([
        getStats(),
        listComplaints({ ...filters, limit: "300" }),
        getHotspots(),
      ]);
      setStats(statsData);
      setComplaints(complaintData);
      setHotspots(hotspotData);
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
    if (!ready) return;
    getWards().then(setWards).catch(() => undefined);
    getConfig().then(setConfig).catch(() => undefined);
  }, [ready]);

  async function doSweep() {
    setSweeping(true);
    try {
      setSweep(await runSlaSweep());
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSweeping(false);
    }
  }

  function set<K extends keyof Filters>(key: K, value: string) {
    setFilters((previous) => ({ ...previous, [key]: value }));
  }

  const activeFilters = Object.entries(filters).filter(([, v]) => v).length;

  if (!ready) return <Spinner label="Checking session" />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">
            Command Center
          </h1>
          <p className="mt-1 text-sm text-mute">
            Every complaint, its evidence verdict, its route and its deadline.
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {stats ? (
            <Pill
              className={
                stats.ai_is_real_model
                  ? "bg-violet-500/15 text-violet-300 ring-violet-500/30"
                  : "bg-ink-800 text-mute ring-ink-600"
              }
              title={
                stats.ai_is_real_model
                  ? "A model-backed provider is handling the AI tasks"
                  : "Deterministic rule engine — no model provider configured"
              }
            >
              AI: {stats.ai_provider}
              {stats.ai_is_real_model ? "" : " (rules)"}
            </Pill>
          ) : null}
          <button
            onClick={doSweep}
            disabled={sweeping}
            className="rounded-lg border border-ink-600 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-ink-800 disabled:opacity-50"
            title="Runs the same SLA sweep the Celery beat schedule calls"
          >
            {sweeping ? "Sweeping…" : "Run SLA sweep"}
          </button>
          <button
            onClick={() => void load()}
            className="rounded-lg border border-ink-600 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-ink-800"
          >
            Refresh
          </button>
        </div>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {sweep ? (
        <div className="rounded-lg border border-ink-600 bg-ink-900/60 px-4 py-2.5 text-xs text-mute">
          Sweep checked <strong className="text-slate-200">{sweep.checked}</strong>{" "}
          complaints · breached{" "}
          <strong className="text-rose-300">
            {sweep.newly_breached.length}
          </strong>{" "}
          · escalated{" "}
          <strong className="text-orange-300">
            {sweep.newly_escalated.length}
          </strong>{" "}
          · cluster counts updated{" "}
          <strong className="text-slate-200">{sweep.clusters_updated}</strong>
          {sweep.newly_breached.length ? (
            <> · {sweep.newly_breached.join(", ")}</>
          ) : null}
        </div>
      ) : null}

      {stats ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Total" value={stats.total} />
          <Stat label="Open" value={stats.open_count} />
          <Stat label="Resolved" value={stats.resolved_count} tone="good" />
          <Stat
            label="SLA breached"
            value={stats.breached_count}
            tone={stats.breached_count ? "danger" : "default"}
          />
          <Stat
            label="Awaiting review"
            value={stats.pending_review_count}
            tone={stats.pending_review_count ? "warn" : "default"}
            hint="Held by the evidence gate or a low-confidence verifier verdict"
          />
          <Stat
            label="In hotspots"
            value={stats.hotspot_count}
            tone={stats.hotspot_count ? "warn" : "default"}
          />
        </div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Geographic view"
            subtitle={`${complaints.length} complaint(s) · ${hotspots.length} hotspot cluster(s) · ${wards.length} wards`}
          />
          <div className="p-4">
            <ComplaintMap
              complaints={complaints}
              wards={wards}
              hotspots={hotspots}
              height="26rem"
            />
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[11px] text-mute">
              {[
                ["#fb7185", "P1"],
                ["#fb923c", "P2"],
                ["#38bdf8", "P3"],
                ["#94a3b8", "P4"],
                ["#34d399", "Resolved"],
                ["#f43f5e", "SLA breached"],
                ["#f97316", "Hotspot cluster"],
              ].map(([colour, label]) => (
                <span key={label} className="flex items-center gap-1.5">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ background: colour }}
                  />
                  {label}
                </span>
              ))}
            </div>
          </div>
        </Card>

        <LiveFeed onEvent={() => void load()} />
      </div>

      <Card>
        <CardHeader
          title="Complaints"
          subtitle={
            activeFilters
              ? `${complaints.length} result(s) · ${activeFilters} filter(s) active`
              : `${complaints.length} result(s)`
          }
          action={
            activeFilters ? (
              <button
                onClick={() => setFilters(EMPTY_FILTERS)}
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
            placeholder="Search description or reference"
            className="min-w-48 flex-1 rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-xs text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
          />
          {(
            [
              ["status", "All statuses", config?.statuses ?? []],
              ["category", "All categories", config?.categories ?? []],
              ["priority", "All priorities", config?.priorities ?? []],
              ["department_code", "All departments", ["BBMP", "BWSSB", "BESCOM"]],
            ] as const
          ).map(([key, label, options]) => (
            <select
              key={key}
              value={filters[key]}
              onChange={(event) => set(key, event.target.value)}
              className="rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
            >
              <option value="">{label}</option>
              {options.map((option) => (
                <option key={option} value={option}>
                  {titleCase(option)}
                </option>
              ))}
            </select>
          ))}
          <select
            value={filters.ward_id}
            onChange={(event) => set("ward_id", event.target.value)}
            className="rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
          >
            <option value="">All wards</option>
            {wards.map((ward) => (
              <option key={ward.id} value={String(ward.id)}>
                {ward.name} ({ward.complaint_count})
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
          <select
            value={filters.needs_review}
            onChange={(event) => set("needs_review", event.target.value)}
            className="rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-xs text-slate-200 focus:border-brand-500 focus:outline-none"
          >
            <option value="">Review: any</option>
            <option value="true">Awaiting review</option>
            <option value="false">Not held</option>
          </select>
        </div>

        {loading ? (
          <Spinner label="Loading complaints" />
        ) : complaints.length === 0 ? (
          <Empty>No complaints match these filters.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-5xl text-left text-sm">
              <thead className="border-b border-ink-700/70 text-[11px] uppercase tracking-wide text-mute">
                <tr>
                  {[
                    "Reference",
                    "Issue",
                    "Category",
                    "Pri",
                    "Ward",
                    "Dept",
                    "Ticket",
                    "Evidence",
                    "SLA",
                    "Status",
                    "Age",
                  ].map((heading) => (
                    <th key={heading} className="whitespace-nowrap px-3 py-2">
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-700/50">
                {complaints.map((complaint) => {
                  const hours = slaHoursRemaining(complaint.sla_due_at);
                  return (
                    <tr key={complaint.id} className="hover:bg-ink-850/60">
                      <td className="whitespace-nowrap px-3 py-2">
                        <RefLink reference={complaint.reference} />
                      </td>
                      <td className="max-w-xs px-3 py-2">
                        <span className="line-clamp-1 text-xs text-slate-300">
                          {complaint.description}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-300">
                        {complaint.category
                          ? (CATEGORY_LABELS[complaint.category] ??
                            complaint.category)
                          : "—"}
                      </td>
                      <td className="px-3 py-2">
                        <PriorityPill priority={complaint.priority} />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-300">
                        {complaint.ward_name ?? "—"}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-300">
                        {complaint.department_code ?? "—"}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-mute">
                        {complaint.external_ticket_id ?? "—"}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <OutcomePill
                          outcome={complaint.authenticity_outcome}
                          score={complaint.authenticity_score}
                        />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs">
                        {complaint.sla_due_at ? (
                          complaint.sla_breached ? (
                            <span className="text-rose-300">
                              breached
                              {hours !== null
                                ? ` ${Math.abs(Math.round(hours))}h`
                                : ""}
                            </span>
                          ) : (
                            <span className="text-mute">
                              {hours !== null ? `${Math.round(hours)}h left` : "—"}
                            </span>
                          )
                        ) : (
                          <span className="text-mute">—</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <div className="flex flex-wrap items-center gap-1">
                          <StatusPill status={complaint.status} />
                          {complaint.needs_human_review ? (
                            <Pill className="bg-amber-500/15 text-amber-300 ring-amber-500/30">
                              review
                            </Pill>
                          ) : null}
                          {complaint.is_hotspot ? (
                            <Pill className="bg-orange-500/15 text-orange-300 ring-orange-500/30">
                              hotspot
                            </Pill>
                          ) : null}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-[11px] text-mute">
                        {relativeTime(complaint.created_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {stats ? (
        <div className="grid gap-5 md:grid-cols-3">
          {(
            [
              ["By status", stats.by_status],
              ["By category", stats.by_category],
              ["By department", stats.by_department],
            ] as const
          ).map(([title, data]) => (
            <Card key={title}>
              <CardHeader title={title} />
              <ul className="divide-y divide-ink-700/50">
                {Object.entries(data)
                  .sort((a, b) => b[1] - a[1])
                  .map(([key, count]) => (
                    <li
                      key={key}
                      className="flex items-center gap-3 px-4 py-2 text-xs"
                    >
                      <span className="text-slate-300">{titleCase(key)}</span>
                      <span className="ml-auto font-mono tabular-nums text-slate-200">
                        {count}
                      </span>
                    </li>
                  ))}
                {Object.keys(data).length === 0 ? (
                  <li className="px-4 py-4 text-center text-xs text-mute">
                    No data yet
                  </li>
                ) : null}
              </ul>
            </Card>
          ))}
        </div>
      ) : null}

      <p className="text-xs text-mute">
        Looking for recurring sites?{" "}
        <Link href="/admin/hotspots" className="text-brand-400 hover:underline">
          Open hotspot analytics →
        </Link>
      </p>
    </div>
  );
}
