/**
 * Renders the evidence signal set.
 *
 * The point of showing every signal — not just a verdict — is that an
 * acceptance or rejection has to be explainable from the exact findings that
 * produced it. A citizen who was rejected can see why; an officer reviewing a
 * held complaint can see what the deterministic layers found before any model
 * was involved.
 */

import type { Evidence, Signal } from "@/lib/types";
import { SEVERITY_STYLES } from "@/lib/format";
import { Field, OutcomePill } from "./ui";

const LAYER_BY_CODE: Record<string, string> = {
  capture_token_valid: "L1 · server-bound capture",
  capture_token_invalid: "L1 · server-bound capture",
  capture_unbound: "L1 · server-bound capture",
  token_replayed: "L1 · server-bound capture",
  source_gallery: "L1 · server-bound capture",
  exif_absent: "L2 · file forensics",
  exif_time_consistent: "L2 · file forensics",
  exif_time_mismatch: "L2 · file forensics",
  exif_gps_consistent: "L2 · file forensics",
  exif_gps_mismatch: "L2 · file forensics",
  editing_software: "L2 · file forensics",
  screenshot_dimensions: "L2 · file forensics",
  media_unreadable: "L2 · file forensics",
  no_photo: "L2 · file forensics",
  ward_exact: "L3 · location plausibility",
  ward_approximate: "L3 · location plausibility",
  no_ward_match: "L3 · location plausibility",
  outside_service_area: "L3 · location plausibility",
  gps_accuracy_ok: "L3 · location plausibility",
  gps_accuracy_poor: "L3 · location plausibility",
  mock_location: "L3 · location plausibility",
  velocity_implausible: "L3 · location plausibility",
  image_reused_exact: "L4 · duplicate detection",
  image_reused_near_exact: "L4 · duplicate detection",
  image_similar: "L4 · duplicate detection",
  reporter_trusted: "L6 · reporter reputation",
  reporter_low_trust: "L6 · reporter reputation",
  reporter_unverified: "L6 · reporter reputation",
};

function SignalRow({ signal }: { signal: Signal }) {
  const tone = SEVERITY_STYLES[signal.severity] ?? SEVERITY_STYLES.info;
  return (
    <li className={`rounded-lg border px-3 py-2 ${tone}`}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-xs font-semibold">{signal.label}</span>
        <code className="rounded bg-black/25 px-1.5 py-0.5 font-mono text-[10px] opacity-80">
          {signal.code}
        </code>
        {LAYER_BY_CODE[signal.code] ? (
          <span className="text-[10px] uppercase tracking-wide opacity-60">
            {LAYER_BY_CODE[signal.code]}
          </span>
        ) : null}
        {signal.delta !== 0 ? (
          <span className="ml-auto font-mono text-xs tabular-nums">
            {signal.delta > 0 ? "+" : ""}
            {signal.delta.toFixed(2)}
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-xs leading-relaxed opacity-90">{signal.detail}</p>
    </li>
  );
}

export function SignalList({ signals }: { signals: Signal[] }) {
  if (!signals.length) {
    return <p className="text-sm text-mute">No signals recorded.</p>;
  }
  const order = { fail: 0, warn: 1, info: 2 } as const;
  const sorted = [...signals].sort(
    (a, b) => order[a.severity] - order[b.severity],
  );
  return (
    <ul className="space-y-2">
      {sorted.map((signal, index) => (
        <SignalRow key={`${signal.code}-${index}`} signal={signal} />
      ))}
    </ul>
  );
}

export function ScoreBar({ score }: { score: number }) {
  const percent = Math.round(Math.max(0, Math.min(1, score)) * 100);
  const tone =
    score >= 0.7
      ? "bg-emerald-500"
      : score >= 0.4
        ? "bg-amber-500"
        : "bg-rose-500";
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] uppercase tracking-wide text-mute">
          Authenticity score
        </span>
        <span className="font-mono text-sm tabular-nums text-slate-200">
          {score.toFixed(2)}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-ink-700">
        <div
          className={`h-full rounded-full transition-all ${tone}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-mute">
        <span>0.00 reject</span>
        <span>0.40 review</span>
        <span>0.70 auto</span>
      </div>
    </div>
  );
}

export function EvidenceDetails({ evidence }: { evidence: Evidence }) {
  const timeDelta = evidence.exif_time_delta_seconds;
  return (
    <div className="space-y-4">
      <ScoreBar score={evidence.authenticity_score} />

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
        <Field
          label="Capture source"
          value={evidence.source}
          hint="camera = captured in-app; gallery = chosen from device storage"
        />
        <Field
          label="Server-bound"
          value={evidence.capture_token_valid ? "Yes" : "No"}
          hint="Whether a valid single-use capture token backed this upload"
        />
        <Field
          label="Server received"
          value={new Date(evidence.server_received_at).toLocaleString("en-IN", {
            day: "2-digit",
            month: "short",
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })}
          hint="Authoritative timestamp — recorded by the server, never the client"
        />
        <Field
          label="EXIF present"
          value={evidence.exif_present ? "Yes" : "No"}
        />
        <Field
          label="EXIF time delta"
          value={
            timeDelta === null
              ? "—"
              : timeDelta < 120
                ? `${timeDelta}s`
                : `${Math.round(timeDelta / 60)} min`
          }
          hint="Gap between the photo's own timestamp and server receipt"
        />
        <Field
          label="EXIF GPS offset"
          value={
            evidence.exif_gps_distance_meters === null
              ? "—"
              : `${Math.round(evidence.exif_gps_distance_meters)} m`
          }
          hint="Distance between the photo's embedded GPS and the reported point"
        />
        <Field
          label="GPS accuracy"
          value={
            evidence.gps_accuracy_meters === null
              ? "—"
              : `${Math.round(evidence.gps_accuracy_meters)} m`
          }
        />
        <Field
          label="Mock location"
          value={evidence.mock_location_flag ? "Flagged" : "No"}
        />
        <Field
          label="Inside serviced ward"
          value={
            evidence.inside_serviced_ward === null
              ? "—"
              : evidence.inside_serviced_ward
                ? "Yes"
                : "No"
          }
        />
        <Field
          label="Editing software"
          value={evidence.editing_software ?? "None"}
        />
        <Field
          label="Image"
          value={
            evidence.image_width
              ? `${evidence.image_width}×${evidence.image_height}`
              : "—"
          }
        />
        <Field
          label="Duplicate of"
          value={
            evidence.duplicate_of_complaint_id
              ? `complaint #${evidence.duplicate_of_complaint_id}${
                  evidence.exact_duplicate ? " (exact)" : ""
                }`
              : "None"
          }
        />
      </dl>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="SHA-256"
          mono
          value={
            evidence.sha256 ? `${evidence.sha256.slice(0, 24)}…` : "—"
          }
          hint="Content hash stored at intake, so later substitution is detectable"
        />
        <Field
          label="Perceptual hash"
          mono
          value={evidence.phash ?? "—"}
          hint="dHash fingerprint — survives rescaling and re-encoding"
        />
      </div>

      {evidence.hard_fail_reason ? (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs leading-relaxed text-rose-200">
          <strong className="font-semibold">Hard failure.</strong>{" "}
          {evidence.hard_fail_reason}
        </div>
      ) : null}

      <div>
        <h3 className="mb-2 flex items-baseline gap-2 text-xs font-semibold uppercase tracking-wide text-mute">
          Signal set
          <OutcomePill
            outcome={evidence.outcome}
            score={evidence.authenticity_score}
          />
        </h3>
        <SignalList signals={evidence.signals} />
      </div>
    </div>
  );
}
