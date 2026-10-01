"use client";

/**
 * Leaflet map over OpenStreetMap tiles.
 *
 * OSM needs no API key, which keeps the prototype runnable with zero
 * configuration. Mapbox or Google Maps drop in here behind the same props.
 *
 * Markers are CircleMarkers rather than pin icons on purpose: they are pure
 * SVG, so there are no image assets to resolve, and radius doubles as a way to
 * show cluster size.
 */

import "leaflet/dist/leaflet.css";
import type { GeoJsonObject } from "geojson";
import { useMemo } from "react";
import {
  CircleMarker,
  GeoJSON,
  MapContainer,
  Popup,
  TileLayer,
  Tooltip,
} from "react-leaflet";
import type { ComplaintListItem, Hotspot, Ward } from "@/lib/types";
import { CATEGORY_LABELS, formatDateTime } from "@/lib/format";

const BENGALURU: [number, number] = [12.9555, 77.6225];

const PRIORITY_COLOUR: Record<string, string> = {
  P1: "#fb7185",
  P2: "#fb923c",
  P3: "#38bdf8",
  P4: "#94a3b8",
};

function colourFor(complaint: ComplaintListItem): string {
  if (complaint.status === "RESOLVED") return "#34d399";
  if (complaint.status === "REJECTED") return "#64748b";
  if (complaint.sla_breached) return "#f43f5e";
  return PRIORITY_COLOUR[complaint.priority ?? "P3"] ?? "#38bdf8";
}

export default function MapView({
  complaints = [],
  wards = [],
  hotspots = [],
  height = "28rem",
  center = BENGALURU,
  zoom = 12,
}: {
  complaints?: ComplaintListItem[];
  wards?: Ward[];
  hotspots?: Hotspot[];
  height?: string;
  center?: [number, number];
  zoom?: number;
}) {
  const wardLayers = useMemo(
    () =>
      wards
        .filter((ward) => ward.boundary && "type" in ward.boundary)
        .map((ward) => ({
          ward,
          geo: ward.boundary as unknown as GeoJsonObject,
        })),
    [wards],
  );

  return (
    <div style={{ height }} className="overflow-hidden rounded-xl">
      <MapContainer
        center={center}
        zoom={zoom}
        scrollWheelZoom
        style={{ height: "100%", width: "100%" }}
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          maxZoom={19}
        />

        {wardLayers.map(({ ward, geo }) => (
          <GeoJSON
            key={`ward-${ward.id}`}
            data={geo}
            style={{
              color: "#2dd4bf",
              weight: 1,
              opacity: 0.35,
              fillColor: "#2dd4bf",
              fillOpacity: ward.complaint_count > 0 ? 0.07 : 0.02,
            }}
          >
            <Tooltip sticky>
              <span className="text-xs">
                Ward {ward.ward_number} · {ward.name}
                <br />
                {ward.zone} zone · {ward.complaint_count} complaint(s)
              </span>
            </Tooltip>
          </GeoJSON>
        ))}

        {hotspots.map((spot) => (
          <CircleMarker
            key={`hotspot-${spot.cluster_id}`}
            center={[spot.latitude, spot.longitude]}
            radius={Math.min(34, 12 + spot.complaint_count * 2.5)}
            pathOptions={{
              color: "#f97316",
              weight: 2,
              fillColor: "#f97316",
              fillOpacity: 0.14,
            }}
          >
            <Popup>
              <strong>Hotspot · {spot.complaint_count} complaints</strong>
              <br />
              {spot.ward_name ?? "Unmapped"}
              <br />
              {spot.categories
                .map((category) => CATEGORY_LABELS[category] ?? category)
                .join(", ")}
              <br />
              {spot.open_count} open · {spot.breached_count} breached
            </Popup>
          </CircleMarker>
        ))}

        {complaints.map((complaint) => (
          <CircleMarker
            key={`complaint-${complaint.id}`}
            center={[complaint.latitude, complaint.longitude]}
            radius={complaint.sla_breached ? 8 : 6}
            pathOptions={{
              color: colourFor(complaint),
              weight: 2,
              fillColor: colourFor(complaint),
              fillOpacity: 0.75,
            }}
          >
            <Popup>
              <strong className="font-mono">{complaint.reference}</strong>
              <br />
              {complaint.category
                ? (CATEGORY_LABELS[complaint.category] ?? complaint.category)
                : "Unclassified"}{" "}
              · {complaint.priority ?? "—"}
              <br />
              {complaint.ward_name ?? "Unmapped"} ·{" "}
              {complaint.department_code ?? "unrouted"}
              <br />
              {complaint.status}
              {complaint.sla_breached ? " · SLA breached" : ""}
              <br />
              <span style={{ opacity: 0.7 }}>
                {formatDateTime(complaint.created_at)}
              </span>
              <br />
              <a href={`/admin/${complaint.reference}`}>Open record →</a>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}
