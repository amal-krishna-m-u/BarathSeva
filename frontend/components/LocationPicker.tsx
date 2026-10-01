"use client";

/**
 * Location capture with a manual fallback.
 *
 * Geolocation is tried first because a measured fix is stronger evidence. But
 * it fails constantly in practice — permission denied, indoors, desktop with
 * no GPS, or the citizen is reporting something they saw earlier from
 * somewhere else. Refusing those reports would lose real civic issues, so the
 * fallback is first-class: search for a place, or drop the pin by hand.
 *
 * What changes is the *evidence weight*, not the acceptance. The chosen method
 * is sent as `location_source` and the backend scores a hand-placed pin lower
 * than a device fix — and raises it again if the photo's own GPS agrees.
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { geocodeDefaults, geocodeReverse, geocodeSearch } from "@/lib/api";
import type {
  GeocodeResult,
  LocationSource,
  ReverseGeocodeResult,
} from "@/lib/types";
import { Pill } from "./ui";

const PickerMap = dynamic(() => import("./LocationPickerMap"), {
  ssr: false,
  loading: () => (
    <div className="grid h-80 place-items-center rounded-xl border border-ink-700/70 bg-ink-900/60 text-sm text-mute">
      Loading map…
    </div>
  ),
});

export interface PickedLocation {
  latitude: number;
  longitude: number;
  accuracy: number | null;
  source: LocationSource;
  address: string | null;
  wardName: string | null;
  insideServiceArea: boolean;
}

const BENGALURU_FALLBACK = { latitude: 12.9716, longitude: 77.5946 };

export default function LocationPicker({
  value,
  onChange,
  maxAccuracyMeters,
}: {
  value: PickedLocation | null;
  onChange: (location: PickedLocation | null) => void;
  maxAccuracyMeters?: number;
}) {
  const [center, setCenter] = useState(BENGALURU_FALLBACK);
  const [mode, setMode] = useState<"idle" | "locating" | "manual">("idle");
  const [geoError, setGeoError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);
  const reverseSeq = useRef(0);

  useEffect(() => {
    geocodeDefaults()
      .then((d) => setCenter(d.center))
      .catch(() => undefined);
  }, []);

  /** Describe a point and record it, whatever put it there. */
  const applyPoint = useCallback(
    async (
      latitude: number,
      longitude: number,
      source: LocationSource,
      accuracy: number | null,
    ) => {
      const sequence = ++reverseSeq.current;
      onChange({
        latitude,
        longitude,
        accuracy,
        source,
        address: null,
        wardName: null,
        insideServiceArea: true,
      });
      setResolving(true);
      try {
        const info: ReverseGeocodeResult = await geocodeReverse(
          latitude,
          longitude,
        );
        // A slower earlier lookup must not overwrite a newer pick.
        if (sequence !== reverseSeq.current) return;
        onChange({
          latitude,
          longitude,
          accuracy,
          source,
          address: info.display_name,
          wardName: info.ward_name,
          insideServiceArea: info.inside_service_area,
        });
      } catch {
        if (sequence !== reverseSeq.current) return;
      } finally {
        if (sequence === reverseSeq.current) setResolving(false);
      }
    },
    [onChange],
  );

  function useMyLocation() {
    if (!navigator.geolocation) {
      setGeoError(
        "This browser does not expose geolocation. Place the pin on the map instead.",
      );
      setMode("manual");
      return;
    }
    setMode("locating");
    setGeoError(null);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setMode("idle");
        setCenter({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        });
        void applyPoint(
          position.coords.latitude,
          position.coords.longitude,
          "device_gps",
          position.coords.accuracy,
        );
      },
      (error) => {
        const reason =
          error.code === error.PERMISSION_DENIED
            ? "Location permission was denied"
            : error.code === error.POSITION_UNAVAILABLE
              ? "Your position could not be determined"
              : "Finding your location timed out";
        setGeoError(
          `${reason}. No problem — search for the place or drop the pin on the map below.`,
        );
        setMode("manual");
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  }

  async function runSearch() {
    const text = query.trim();
    if (text.length < 3) return;
    setSearching(true);
    setSearchError(null);
    try {
      const found = await geocodeSearch(text);
      setResults(found);
      if (found.length === 0) {
        setSearchError(
          "No match in the serviced area. Try a nearby landmark, or place the pin directly.",
        );
      }
    } catch (error) {
      setSearchError((error as Error).message);
    } finally {
      setSearching(false);
    }
  }

  function chooseResult(result: GeocodeResult) {
    setResults([]);
    setQuery(result.display_name.split(",").slice(0, 2).join(","));
    setCenter({ latitude: result.latitude, longitude: result.longitude });
    void applyPoint(result.latitude, result.longitude, "geocoded", null);
  }

  const showManual = mode === "manual" || !!value;
  const accuracyTooCoarse =
    value?.accuracy != null &&
    maxAccuracyMeters != null &&
    value.accuracy > maxAccuracyMeters;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={useMyLocation}
          disabled={mode === "locating"}
          className="rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-ink-950 transition hover:bg-brand-500 disabled:opacity-50"
        >
          {mode === "locating" ? "Getting location…" : "Use my location"}
        </button>
        <button
          type="button"
          onClick={() => setMode("manual")}
          className="rounded-lg border border-ink-600 px-3.5 py-2 text-sm text-slate-300 transition hover:bg-ink-800"
        >
          I&apos;m not there — pick on map
        </button>
      </div>

      {geoError ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs leading-relaxed text-amber-200">
          {geoError}
        </p>
      ) : null}

      {showManual ? (
        <div className="space-y-3">
          {/* A div, not a form: this component is rendered inside the report
              form, and a nested <form> is invalid HTML that React refuses to
              render. Enter is handled explicitly instead. */}
          <div className="flex flex-wrap gap-2">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void runSearch();
                }
              }}
              placeholder="Search a road, landmark or area"
              className="min-w-48 flex-1 rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm text-slate-100 placeholder:text-mute/70 focus:border-brand-500 focus:outline-none"
            />
            <button
              type="button"
              onClick={() => void runSearch()}
              disabled={searching || query.trim().length < 3}
              className="rounded-lg border border-ink-600 px-3.5 py-2 text-sm text-slate-300 transition hover:bg-ink-800 disabled:opacity-50"
            >
              {searching ? "Searching…" : "Search"}
            </button>
          </div>

          {searchError ? (
            <p className="text-xs text-amber-300">{searchError}</p>
          ) : null}

          {results.length ? (
            <ul className="divide-y divide-ink-700/70 overflow-hidden rounded-lg border border-ink-600">
              {results.map((result) => (
                <li key={`${result.latitude}-${result.longitude}`}>
                  <button
                    type="button"
                    onClick={() => chooseResult(result)}
                    className="w-full px-3 py-2 text-left text-xs leading-relaxed text-slate-300 transition hover:bg-ink-800"
                  >
                    {result.display_name}
                    {!result.inside_service_area ? (
                      <span className="ml-2 text-rose-300">
                        outside serviced area
                      </span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}

          <PickerMap
            latitude={value?.latitude ?? center.latitude}
            longitude={value?.longitude ?? center.longitude}
            onPick={(lat, lon) => {
              void applyPoint(lat, lon, "map_picked", null);
            }}
          />
          <p className="text-xs text-mute">
            Tap anywhere on the map to place the pin exactly where the problem
            is.
          </p>
        </div>
      ) : null}

      {value ? (
        <div className="rounded-lg border border-ink-600 bg-ink-850/60 px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <Pill
              className={
                value.source === "device_gps"
                  ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30"
                  : "bg-amber-500/15 text-amber-300 ring-amber-500/30"
              }
              title={
                value.source === "device_gps"
                  ? "Measured by the device — strongest location evidence"
                  : "Asserted by you rather than measured; weighted lower, and raised again if your photo's GPS agrees"
              }
            >
              {value.source === "device_gps"
                ? "Device GPS"
                : value.source === "geocoded"
                  ? "From address search"
                  : "Placed on map"}
            </Pill>
            {value.accuracy != null ? (
              <Pill
                className={
                  accuracyTooCoarse
                    ? "bg-amber-500/15 text-amber-300 ring-amber-500/30"
                    : "bg-ink-800 text-mute ring-ink-600"
                }
              >
                ±{Math.round(value.accuracy)} m
              </Pill>
            ) : null}
            {value.wardName ? (
              <Pill className="bg-ink-800 text-slate-300 ring-ink-600">
                Ward: {value.wardName}
              </Pill>
            ) : null}
            {!value.insideServiceArea ? (
              <Pill className="bg-rose-500/15 text-rose-300 ring-rose-500/30">
                Outside serviced area
              </Pill>
            ) : null}
            <span className="ml-auto font-mono text-[11px] text-mute">
              {value.latitude.toFixed(5)}, {value.longitude.toFixed(5)}
            </span>
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-mute">
            {resolving
              ? "Looking up the address…"
              : (value.address ?? "Address unavailable — the coordinate is what matters.")}
          </p>
          {accuracyTooCoarse ? (
            <p className="mt-1 text-xs text-amber-300">
              This fix is coarser than {maxAccuracyMeters} m, which cannot
              support a precise ward assignment. Consider placing the pin
              manually instead.
            </p>
          ) : null}
          {!value.insideServiceArea ? (
            <p className="mt-1 text-xs text-rose-300">
              This point is outside the serviced city, so the report will be
              rejected. Move the pin inside Bengaluru.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
