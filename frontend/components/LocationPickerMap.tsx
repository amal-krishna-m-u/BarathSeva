"use client";

/** The Leaflet half of the location picker: click or drag to place the pin. */

import "leaflet/dist/leaflet.css";
import { useEffect } from "react";
import {
  CircleMarker,
  MapContainer,
  TileLayer,
  useMap,
  useMapEvents,
} from "react-leaflet";

function ClickToPlace({
  onPick,
}: {
  onPick: (lat: number, lon: number) => void;
}) {
  useMapEvents({
    click(event) {
      onPick(event.latlng.lat, event.latlng.lng);
    },
  });
  return null;
}

/** Re-centre when the parent moves the point (search result, GPS fix). */
function Recenter({
  latitude,
  longitude,
  zoom,
}: {
  latitude: number;
  longitude: number;
  zoom?: number;
}) {
  const map = useMap();
  useEffect(() => {
    map.setView([latitude, longitude], zoom ?? map.getZoom(), {
      animate: true,
    });
  }, [map, latitude, longitude, zoom]);
  return null;
}

export default function LocationPickerMap({
  latitude,
  longitude,
  onPick,
  height = "20rem",
  zoom = 16,
}: {
  latitude: number;
  longitude: number;
  onPick: (lat: number, lon: number) => void;
  height?: string;
  zoom?: number;
}) {
  return (
    <div style={{ height }} className="overflow-hidden rounded-xl">
      <MapContainer
        center={[latitude, longitude]}
        zoom={zoom}
        scrollWheelZoom
        style={{ height: "100%", width: "100%", cursor: "crosshair" }}
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          maxZoom={19}
        />
        <ClickToPlace onPick={onPick} />
        <Recenter latitude={latitude} longitude={longitude} />

        {/* Two rings: a solid centre for the exact point, a halo for visibility. */}
        <CircleMarker
          center={[latitude, longitude]}
          radius={18}
          pathOptions={{
            color: "#2dd4bf",
            weight: 1,
            fillColor: "#2dd4bf",
            fillOpacity: 0.12,
          }}
        />
        <CircleMarker
          center={[latitude, longitude]}
          radius={7}
          pathOptions={{
            color: "#0b0f17",
            weight: 2,
            fillColor: "#2dd4bf",
            fillOpacity: 1,
          }}
        />
      </MapContainer>
    </div>
  );
}
