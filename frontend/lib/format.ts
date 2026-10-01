/** Presentation helpers. Colour maps live here so status styling is consistent. */

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.round((Date.now() - then) / 1000);
  const future = seconds < 0;
  const abs = Math.abs(seconds);

  const units: [number, string][] = [
    [60, "s"],
    [3600, "m"],
    [86400, "h"],
    [2592000, "d"],
  ];
  let text = `${abs}s`;
  if (abs < 60) text = `${abs}s`;
  else if (abs < 3600) text = `${Math.round(abs / 60)}m`;
  else if (abs < 86400) text = `${Math.round(abs / 3600)}h`;
  else text = `${Math.round(abs / 86400)}d`;
  void units;
  return future ? `in ${text}` : `${text} ago`;
}

/** Hours until the SLA deadline; negative means overdue. */
export function slaHoursRemaining(dueAt: string | null): number | null {
  if (!dueAt) return null;
  const due = new Date(dueAt).getTime();
  if (Number.isNaN(due)) return null;
  return (due - Date.now()) / 3_600_000;
}

export const STATUS_STYLES: Record<string, string> = {
  SUBMITTED: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  PENDING_REVIEW: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  REJECTED: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  VERIFIED: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  CLASSIFIED: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  LOCATED: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  DISPATCHED: "bg-indigo-500/15 text-indigo-300 ring-indigo-500/30",
  IN_PROGRESS: "bg-indigo-500/15 text-indigo-300 ring-indigo-500/30",
  ESCALATED: "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  RESOLVED: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
};

export const PRIORITY_STYLES: Record<string, string> = {
  P1: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  P2: "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  P3: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  P4: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
};

export const OUTCOME_STYLES: Record<string, string> = {
  AUTO_ACCEPT: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  ACCEPT_FLAGGED: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  HUMAN_REVIEW: "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  HARD_FAIL: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
};

export const SEVERITY_STYLES: Record<string, string> = {
  info: "border-emerald-500/30 bg-emerald-500/5 text-emerald-200",
  warn: "border-amber-500/30 bg-amber-500/5 text-amber-200",
  fail: "border-rose-500/40 bg-rose-500/10 text-rose-200",
};

export const DEPARTMENT_LABELS: Record<string, string> = {
  BBMP: "Bruhat Bengaluru Mahanagara Palike",
  BWSSB: "Bangalore Water Supply & Sewerage Board",
  BESCOM: "Bangalore Electricity Supply Company",
};

export const CATEGORY_LABELS: Record<string, string> = {
  POTHOLE: "Pothole",
  ROAD_DAMAGE: "Road damage",
  WATER_LEAK: "Water leak",
  PIPELINE_BURST: "Burst pipeline",
  DRAINAGE: "Drainage",
  SEWAGE: "Sewage",
  GARBAGE: "Garbage",
  STREETLIGHT: "Streetlight",
  POWER_OUTAGE: "Power outage",
  OTHER: "Other",
};

export const AGENT_LABELS: Record<string, string> = {
  evidence_gate: "Evidence gate",
  verifier: "Verifier",
  classifier: "Classifier",
  geocluster: "GeoCluster",
  dispatcher: "Dispatcher",
  sla_monitor: "SLA monitor",
  social_amplifier: "Social amplifier",
  resolution_update: "Resolution update",
};

export function titleCase(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .toLowerCase()
    .split(/[\s_]+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/** Stable per-browser device id, used as a capture-token binding. */
export function deviceId(): string {
  if (typeof window === "undefined") return "server";
  const key = "barathseva.device_id";
  let value = window.localStorage.getItem(key);
  if (!value) {
    value = `web-${Math.random().toString(36).slice(2, 12)}`;
    window.localStorage.setItem(key, value);
  }
  return value;
}
