"use client";

/**
 * Live audit feed over Server-Sent Events.
 *
 * This is what makes the command center update without polling in the local
 * stack. A Supabase deployment would subscribe to Supabase Realtime instead —
 * the data source is the same `complaint_events` table either way.
 *
 * Note: EventSource cannot send custom headers, so this stream is not covered
 * by the X-Admin-Key gate. Production would authenticate it with a cookie or a
 * signed query parameter.
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { API_BASE } from "@/lib/api";
import { formatDateTime, titleCase } from "@/lib/format";
import type { LiveEvent } from "@/lib/types";
import { Card, CardHeader, Pill } from "./ui";

const MAX_EVENTS = 40;

const EVENT_TONE: Record<string, string> = {
  CREATED: "text-slate-300",
  EVIDENCE_CHECKED: "text-sky-300",
  VERIFIED: "text-emerald-300",
  REJECTED: "text-rose-300",
  HELD_FOR_REVIEW: "text-amber-300",
  CLASSIFIED: "text-sky-300",
  LOCATED: "text-sky-300",
  DISPATCHED: "text-indigo-300",
  SLA_STARTED: "text-slate-300",
  SLA_BREACHED: "text-rose-300",
  ESCALATED: "text-orange-300",
  SOCIAL_POSTED: "text-violet-300",
  RESOLVED: "text-emerald-300",
  REVIEW_DECISION: "text-amber-300",
};

export default function LiveFeed({ onEvent }: { onEvent?: () => void }) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const callbackRef = useRef(onEvent);
  callbackRef.current = onEvent;

  useEffect(() => {
    const source = new EventSource(`${API_BASE}/api/admin/stream`);

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as LiveEvent;
        setEvents((previous) => [event, ...previous].slice(0, MAX_EVENTS));
        callbackRef.current?.();
      } catch {
        /* keep-alive comment or malformed frame */
      }
    };

    return () => source.close();
  }, []);

  return (
    <Card className="flex max-h-[32rem] flex-col">
      <CardHeader
        title="Live activity"
        subtitle="Server-sent events from the audit trail"
        action={
          <Pill
            className={
              connected
                ? "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30"
                : "bg-rose-500/15 text-rose-300 ring-rose-500/30"
            }
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-emerald-400 live-dot" : "bg-rose-400"}`}
            />
            {connected ? "Live" : "Disconnected"}
          </Pill>
        }
      />
      <ol className="flex-1 divide-y divide-ink-700/70 overflow-y-auto">
        {events.length === 0 ? (
          <li className="px-4 py-8 text-center text-xs text-mute">
            Waiting for activity. Submit a complaint to see the pipeline fire.
          </li>
        ) : (
          events.map((event) => (
            <li key={event.id} className="px-4 py-2">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span
                  className={`text-xs font-semibold ${EVENT_TONE[event.event_type] ?? "text-slate-300"}`}
                >
                  {titleCase(event.event_type)}
                </span>
                <Link
                  href={`/admin/${event.reference}`}
                  className="font-mono text-[11px] text-brand-400 hover:underline"
                >
                  {event.reference}
                </Link>
                <span className="ml-auto font-mono text-[10px] text-mute">
                  {formatDateTime(event.created_at)}
                </span>
              </div>
              {event.message ? (
                <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-mute">
                  {event.message}
                </p>
              ) : null}
            </li>
          ))
        )}
      </ol>
    </Card>
  );
}
