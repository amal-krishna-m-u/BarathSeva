"use client";

/**
 * Citizen reporting flow.
 *
 * The order of operations is the whole point of evidence Layer 1: the client
 * asks the server for a short-lived capture token *before* the camera opens,
 * and the upload must present it. A browser canvas capture carries no EXIF at
 * all, which is exactly why the token — not the photo's own metadata — is what
 * binds the evidence to a known moment.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getConfig,
  requestCaptureToken,
  submitComplaint,
} from "@/lib/api";
import { deviceId } from "@/lib/format";
import type { PublicConfig, SubmitResponse } from "@/lib/types";
import { SignalList, ScoreBar } from "./EvidencePanel";
import { Card, CardHeader, ErrorNote, Field, OutcomePill, Pill } from "./ui";

type Fix = { latitude: number; longitude: number; accuracy: number };
type Shot = { blob: Blob; url: string; source: "camera" | "gallery" };

const EXAMPLES = [
  "Large pothole near Koramangala 5th Block, very deep, a two wheeler already skidded here",
  "Water gushing from a burst pipeline on 100 ft road, whole street flooded since morning",
  "Street light near the park has been off for three days, the whole stretch is dark",
  "Garbage has not been collected for a week near the main gate",
];

export default function ReportForm() {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [description, setDescription] = useState("");
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");

  const [fix, setFix] = useState<Fix | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);

  const [shot, setShot] = useState<Shot | null>(null);
  const [cameraOn, setCameraOn] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SubmitResponse | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    getConfig().then(setConfig).catch(() => undefined);
  }, []);

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setCameraOn(false);
  }, []);

  useEffect(() => stopCamera, [stopCamera]);

  function locate() {
    if (!navigator.geolocation) {
      setLocationError("This browser does not expose geolocation.");
      return;
    }
    setLocating(true);
    setLocationError(null);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setFix({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
        });
        setLocating(false);
      },
      (err) => {
        setLocationError(
          `${err.message}. Location is required — ward and department routing both derive from it.`,
        );
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  }

  async function startCamera() {
    setCameraError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      });
      streamRef.current = stream;
      setCameraOn(true);
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => undefined);
      }
    } catch (err) {
      setCameraError(
        `Camera unavailable (${(err as Error).name}). You can still attach a photo from your device — it will be marked as a gallery upload and weighted lower.`,
      );
      setCameraOn(false);
    }
  }

  function capture() {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        if (shot) URL.revokeObjectURL(shot.url);
        setShot({ blob, url: URL.createObjectURL(blob), source: "camera" });
        stopCamera();
      },
      "image/jpeg",
      0.92,
    );
  }

  function pickFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (shot) URL.revokeObjectURL(shot.url);
    setShot({
      blob: file,
      url: URL.createObjectURL(file),
      source: "gallery",
    });
    stopCamera();
  }

  function reset() {
    if (shot) URL.revokeObjectURL(shot.url);
    setShot(null);
    setResult(null);
    setError(null);
    setDescription("");
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!fix) {
      setError("Share your location first.");
      return;
    }
    if (description.trim().length < 5) {
      setError("Describe the problem in a few words.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      // Layer 1: bind this capture to a server-issued, single-use token.
      const token = await requestCaptureToken({
        device_id: deviceId(),
        latitude: fix.latitude,
        longitude: fix.longitude,
        phone: phone.trim() || undefined,
      });

      const form = new FormData();
      form.set("description", description.trim());
      form.set("latitude", String(fix.latitude));
      form.set("longitude", String(fix.longitude));
      form.set("channel", "web");
      form.set("declared_source", shot?.source ?? "camera");
      form.set("gps_accuracy_meters", String(Math.round(fix.accuracy)));
      form.set("mock_location", "false");
      if (shot?.source === "camera") form.set("capture_token", token.token);
      if (phone.trim()) {
        form.set("reporter_phone", phone.trim());
        form.set("reporter_name", name.trim() || "Citizen");
      }
      if (shot) form.set("photo", shot.blob, "evidence.jpg");

      setResult(await submitComplaint(form));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return <ResultView result={result} onReset={reset} />;
  }

  const ready = Boolean(fix) && description.trim().length >= 5;

  return (
    <form onSubmit={submit} className="grid gap-5 lg:grid-cols-5">
      <div className="space-y-5 lg:col-span-3">
        {/* ---------------------------------------------------- step 1 */}
        <Card>
          <CardHeader
            title="1 · Where is the problem?"
            subtitle="Ward and department routing are both derived from this coordinate."
            action={
              fix ? (
                <Pill className="bg-emerald-500/15 text-emerald-300 ring-emerald-500/30">
                  ±{Math.round(fix.accuracy)} m
                </Pill>
              ) : null
            }
          />
          <div className="p-4">
            {fix ? (
              <dl className="grid grid-cols-3 gap-3">
                <Field label="Latitude" mono value={fix.latitude.toFixed(6)} />
                <Field label="Longitude" mono value={fix.longitude.toFixed(6)} />
                <Field
                  label="Accuracy"
                  value={
                    config && fix.accuracy > config.gps_accuracy_max_meters ? (
                      <span className="text-amber-300">
                        {Math.round(fix.accuracy)} m — too imprecise
                      </span>
                    ) : (
                      `${Math.round(fix.accuracy)} m`
                    )
                  }
                />
              </dl>
            ) : (
              <p className="text-sm text-mute">
                No location yet.
                {config
                  ? ` Fixes coarser than ${config.gps_accuracy_max_meters} m are flagged.`
                  : ""}
              </p>
            )}
            <button
              type="button"
              onClick={locate}
              disabled={locating}
              className="mt-3 rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-ink-950 transition hover:bg-brand-500 disabled:opacity-50"
            >
              {locating
                ? "Getting location…"
                : fix
                  ? "Refresh location"
                  : "Use my location"}
            </button>
            {locationError ? (
              <p className="mt-2 text-xs text-rose-300">{locationError}</p>
            ) : null}
          </div>
        </Card>

        {/* ---------------------------------------------------- step 2 */}
        <Card>
          <CardHeader
            title="2 · Photograph the issue"
            subtitle="Capturing in-app binds the photo to a server-issued token, which is what bounds how old the evidence can be."
          />
          <div className="space-y-3 p-4">
            {shot ? (
              <div className="space-y-3">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={shot.url}
                  alt="Captured evidence"
                  className="max-h-72 w-full rounded-lg object-contain ring-1 ring-ink-600"
                />
                <div className="flex flex-wrap items-center gap-2">
                  <Pill
                    className={
                      shot.source === "camera"
                        ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30"
                        : "bg-amber-500/15 text-amber-300 ring-amber-500/30"
                    }
                  >
                    {shot.source === "camera"
                      ? "In-app capture · server-bound"
                      : "Gallery upload · weighted lower"}
                  </Pill>
                  <button
                    type="button"
                    onClick={() => {
                      URL.revokeObjectURL(shot.url);
                      setShot(null);
                    }}
                    className="rounded-lg border border-ink-600 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-ink-800"
                  >
                    Retake
                  </button>
                </div>
              </div>
            ) : cameraOn ? (
              <div className="space-y-3">
                <video
                  ref={videoRef}
                  playsInline
                  muted
                  className="max-h-72 w-full rounded-lg bg-black object-contain ring-1 ring-ink-600"
                />
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={capture}
                    className="rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-ink-950 transition hover:bg-brand-500"
                  >
                    Take photo
                  </button>
                  <button
                    type="button"
                    onClick={stopCamera}
                    className="rounded-lg border border-ink-600 px-3.5 py-2 text-sm text-slate-300 transition hover:bg-ink-800"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={startCamera}
                  className="rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-ink-950 transition hover:bg-brand-500"
                >
                  Open camera
                </button>
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  className="rounded-lg border border-ink-600 px-3.5 py-2 text-sm text-slate-300 transition hover:bg-ink-800"
                >
                  Attach from device
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  accept="image/*"
                  onChange={pickFile}
                  className="hidden"
                />
              </div>
            )}
            {cameraError ? (
              <p className="text-xs text-amber-300">{cameraError}</p>
            ) : null}
            <p className="text-xs leading-relaxed text-mute">
              A browser capture carries no EXIF metadata, so the server-issued
              token is what establishes when this photo was taken. A text-only
              report is accepted but scores lower, since nothing corroborates
              the description visually.
            </p>
          </div>
        </Card>

        {/* ---------------------------------------------------- step 3 */}
        <Card>
          <CardHeader
            title="3 · Describe it"
            subtitle="Plain language is fine — classification and routing are automatic."
          />
          <div className="space-y-3 p-4">
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={4}
              placeholder="e.g. Large pothole near Koramangala 5th Block, very deep, a two wheeler already skidded here"
              className="w-full resize-y rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
            />
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setDescription(example)}
                  className="rounded-full border border-ink-600 px-2.5 py-1 text-[11px] text-mute transition hover:bg-ink-800 hover:text-slate-200"
                >
                  {example.slice(0, 38)}…
                </button>
              ))}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <input
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                placeholder="Phone (optional, e.g. +919000000001)"
                className="rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
              />
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Name (optional)"
                className="rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
              />
            </div>
            <p className="text-xs text-mute">
              Reporting anonymously is allowed. Giving a phone number builds a
              trust score over confirmed reports, which raises how far your
              reports get automatically.
            </p>
          </div>
        </Card>

        {error ? <ErrorNote>{error}</ErrorNote> : null}

        <button
          type="submit"
          disabled={!ready || submitting}
          className="w-full rounded-xl bg-brand-500 px-4 py-3 text-sm font-semibold text-ink-950 transition hover:bg-brand-400 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting
            ? "Running the pipeline…"
            : "Submit — one message starts the whole workflow"}
        </button>
      </div>

      {/* ------------------------------------------------------- sidebar */}
      <aside className="space-y-5 lg:col-span-2">
        <Card>
          <CardHeader title="What happens when you submit" />
          <ol className="space-y-2.5 p-4 text-xs leading-relaxed text-mute">
            {[
              ["Evidence checks", "Capture token, EXIF forensics, GPS plausibility and image-reuse detection run before anything else."],
              ["Verifier", "Decides whether the text and photo agree and describe a real civic issue."],
              ["Classifier", "Assigns a category and priority, constrained by policy rules."],
              ["GeoCluster", "PostGIS resolves your ward and finds corroborating reports nearby."],
              ["Dispatcher", "Looks up the responsible department and opens a ticket."],
              ["SLA monitor", "Starts the resolution clock and escalates if it is missed."],
            ].map(([title, body], index) => (
              <li key={title} className="flex gap-2.5">
                <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-ink-800 font-mono text-[10px] text-brand-400 ring-1 ring-ink-600">
                  {index + 1}
                </span>
                <span>
                  <strong className="font-semibold text-slate-300">
                    {title}.
                  </strong>{" "}
                  {body}
                </span>
              </li>
            ))}
          </ol>
        </Card>

        {config ? (
          <Card>
            <CardHeader
              title="Live policy"
              subtitle="Served by the backend — the UI does not keep its own copy."
            />
            <dl className="grid grid-cols-2 gap-3 p-4">
              <Field
                label="Capture token TTL"
                value={`${config.capture_token_ttl_seconds}s`}
              />
              <Field
                label="Max GPS radius"
                value={`${config.gps_accuracy_max_meters} m`}
              />
              <Field
                label="Auto-accept at"
                value={config.authenticity_auto_accept.toFixed(2)}
              />
              <Field
                label="Review floor"
                value={config.authenticity_review_floor.toFixed(2)}
              />
              <Field
                label="Cluster radius"
                value={`${config.cluster_radius_meters} m`}
              />
              <Field
                label="Hotspot at"
                value={`${config.hotspot_min_complaints} reports`}
              />
            </dl>
          </Card>
        ) : null}
      </aside>
    </form>
  );
}

function ResultView({
  result,
  onReset,
}: {
  result: SubmitResponse;
  onReset: () => void;
}) {
  const tone = !result.accepted
    ? "border-rose-500/40 bg-rose-500/10"
    : result.needs_human_review
      ? "border-amber-500/40 bg-amber-500/10"
      : "border-emerald-500/40 bg-emerald-500/10";

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className={`rounded-xl border px-5 py-4 ${tone}`}>
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono text-lg font-semibold text-white">
            {result.reference}
          </span>
          <OutcomePill
            outcome={result.authenticity_outcome}
            score={result.authenticity_score}
          />
          {result.is_hotspot ? (
            <Pill className="bg-orange-500/15 text-orange-300 ring-orange-500/30">
              Hotspot
            </Pill>
          ) : null}
        </div>
        <p className="mt-2 text-sm leading-relaxed text-slate-200">
          {result.message}
        </p>
      </div>

      {result.accepted && !result.needs_human_review ? (
        <Card>
          <CardHeader title="Routed" />
          <dl className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-3">
            <Field label="Category" value={result.category} />
            <Field label="Priority" value={result.priority} />
            <Field label="Ward" value={result.ward_name} />
            <Field label="Department" value={result.department_code} />
            <Field label="Ticket" mono value={result.external_ticket_id} />
            <Field
              label="Resolve by"
              value={
                result.sla_due_at
                  ? new Date(result.sla_due_at).toLocaleString("en-IN", {
                      day: "2-digit",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                      hour12: false,
                    })
                  : "—"
              }
            />
            <Field label="Nearby reports" value={result.nearby_count} />
          </dl>
        </Card>
      ) : null}

      <Card>
        <CardHeader
          title="Evidence checks"
          subtitle="Every signal is stored, so this decision can be re-explained later."
        />
        <div className="space-y-4 p-4">
          <ScoreBar score={result.authenticity_score} />
          <SignalList signals={result.evidence_signals} />
        </div>
      </Card>

      {result.pipeline_trace.length ? (
        <Card>
          <CardHeader title="Pipeline path" />
          <div className="flex flex-wrap items-center gap-1.5 p-4">
            {result.pipeline_trace.map((node, index) => (
              <span key={`${node}-${index}`} className="flex items-center gap-1.5">
                {index > 0 ? <span className="text-mute">→</span> : null}
                <code className="rounded bg-ink-800 px-2 py-1 font-mono text-[11px] text-brand-400 ring-1 ring-ink-600">
                  {node}
                </code>
              </span>
            ))}
          </div>
        </Card>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Link
          href={`/track/${result.reference}`}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-ink-950 transition hover:bg-brand-500"
        >
          Track this complaint
        </Link>
        <button
          onClick={onReset}
          className="rounded-lg border border-ink-600 px-4 py-2 text-sm text-slate-300 transition hover:bg-ink-800"
        >
          Report another issue
        </button>
      </div>
    </div>
  );
}
