"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import ComplaintMap from "@/components/ComplaintMap";
import { getHotspots, getSocialPosts, getWards, listComplaints } from "@/lib/api";
import { CATEGORY_LABELS, formatDateTime, titleCase } from "@/lib/format";
import type {
  ComplaintListItem,
  Hotspot,
  SocialPost,
  Ward,
} from "@/lib/types";
import {
  Card,
  CardHeader,
  Empty,
  ErrorNote,
  Pill,
  Spinner,
  Stat,
} from "@/components/ui";

const WINDOWS = [
  [24, "24 hours"],
  [72, "3 days"],
  [168, "7 days"],
  [720, "30 days"],
] as const;

export default function HotspotsPage() {
  const [windowHours, setWindowHours] = useState<number>(720);
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [complaints, setComplaints] = useState<ComplaintListItem[]>([]);
  const [wards, setWards] = useState<Ward[]>([]);
  const [posts, setPosts] = useState<SocialPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([
      getHotspots(windowHours),
      listComplaints({ limit: "300" }),
      getWards(),
      getSocialPosts(),
    ])
      .then(([hotspotData, complaintData, wardData, postData]) => {
        if (!active) return;
        setHotspots(hotspotData);
        setComplaints(complaintData);
        setWards(wardData);
        setPosts(postData);
        setError(null);
      })
      .catch((err) => active && setError((err as Error).message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [windowHours]);

  const totalInClusters = hotspots.reduce(
    (sum, spot) => sum + spot.complaint_count,
    0,
  );
  const breachedInClusters = hotspots.reduce(
    (sum, spot) => sum + spot.breached_count,
    0,
  );

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">
            Hotspot analytics
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-mute">
            Density-based clusters computed in PostGIS with{" "}
            <code className="font-mono text-xs text-brand-400">
              ST_ClusterDBSCAN
            </code>
            . These are the recurring sites that need a scheduled inspection
            rather than another one-off repair.
          </p>
        </div>
        <div className="ml-auto flex gap-1.5">
          {WINDOWS.map(([hours, label]) => (
            <button
              key={hours}
              onClick={() => setWindowHours(hours)}
              className={`rounded-lg px-3 py-1.5 text-xs transition ${
                windowHours === hours
                  ? "bg-brand-600 font-medium text-ink-950"
                  : "border border-ink-600 text-slate-300 hover:bg-ink-800"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Clusters" value={hotspots.length} />
        <Stat label="Complaints clustered" value={totalInClusters} />
        <Stat
          label="Breached in clusters"
          value={breachedInClusters}
          tone={breachedInClusters ? "danger" : "default"}
        />
        <Stat
          label="Largest cluster"
          value={hotspots[0]?.complaint_count ?? 0}
          tone={hotspots.length ? "warn" : "default"}
        />
      </div>

      <Card>
        <CardHeader
          title="Cluster map"
          subtitle="Orange rings scale with cluster size"
        />
        <div className="p-4">
          <ComplaintMap
            complaints={complaints}
            wards={wards}
            hotspots={hotspots}
            height="30rem"
          />
        </div>
      </Card>

      <Card>
        <CardHeader title="Clusters" subtitle={`Window: last ${windowHours}h`} />
        {loading ? (
          <Spinner label="Computing clusters" />
        ) : hotspots.length === 0 ? (
          <Empty>
            No cluster reaches the minimum-complaint threshold in this window.
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-ink-700/70 text-[11px] uppercase tracking-wide text-mute">
                <tr>
                  {[
                    "Cluster",
                    "Complaints",
                    "Open",
                    "Breached",
                    "Ward",
                    "Categories",
                    "Centroid",
                  ].map((heading) => (
                    <th key={heading} className="whitespace-nowrap px-3 py-2">
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-700/50">
                {hotspots.map((spot) => (
                  <tr key={spot.cluster_id} className="hover:bg-ink-850/60">
                    <td className="px-3 py-2 font-mono text-xs text-mute">
                      #{spot.cluster_id}
                    </td>
                    <td className="px-3 py-2">
                      <span className="font-semibold tabular-nums text-white">
                        {spot.complaint_count}
                      </span>
                    </td>
                    <td className="px-3 py-2 tabular-nums text-slate-300">
                      {spot.open_count}
                    </td>
                    <td className="px-3 py-2 tabular-nums">
                      {spot.breached_count ? (
                        <span className="text-rose-300">
                          {spot.breached_count}
                        </span>
                      ) : (
                        <span className="text-mute">0</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-300">
                      {spot.ward_name ?? "—"}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1">
                        {spot.categories.map((category) => (
                          <Pill
                            key={category}
                            className="bg-ink-800 text-slate-300 ring-ink-600"
                          >
                            {CATEGORY_LABELS[category] ?? category}
                          </Pill>
                        ))}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-mute">
                      {spot.latitude.toFixed(5)}, {spot.longitude.toFixed(5)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Accountability content"
          subtitle="Generated for eligible complaints by policy rule, then worded by the model. Held unpublished in the prototype."
        />
        <ul className="divide-y divide-ink-700/70">
          {posts.map((post) => (
            <li key={post.id} className="px-4 py-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <Pill
                  className={
                    post.kind === "breach"
                      ? "bg-rose-500/15 text-rose-300 ring-rose-500/30"
                      : "bg-violet-500/15 text-violet-300 ring-violet-500/30"
                  }
                >
                  {titleCase(post.kind)}
                </Pill>
                <Link
                  href={`/admin/${post.complaint_reference}`}
                  className="font-mono text-xs text-brand-400 hover:underline"
                >
                  {post.complaint_reference}
                </Link>
                <Pill className="bg-ink-800 text-mute ring-ink-600">
                  {post.is_published ? "published" : "unpublished"}
                </Pill>
                <span className="ml-auto font-mono text-[11px] text-mute">
                  {formatDateTime(post.created_at)}
                </span>
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-slate-200">
                {post.content}
              </p>
              {post.eligibility_reason ? (
                <p className="mt-1 text-[11px] leading-relaxed text-mute">
                  {post.eligibility_reason}
                </p>
              ) : null}
            </li>
          ))}
          {posts.length === 0 ? (
            <li className="px-4 py-6 text-center text-sm text-mute">
              No complaint has met the amplification policy yet.
            </li>
          ) : null}
        </ul>
      </Card>
    </div>
  );
}
