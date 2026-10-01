"use client";

/** Leaflet touches `window` on import, so the map is loaded client-side only. */

import dynamic from "next/dynamic";

const MapView = dynamic(() => import("./MapView"), {
  ssr: false,
  loading: () => (
    <div className="grid h-[28rem] place-items-center rounded-xl border border-ink-700/70 bg-ink-900/60 text-sm text-mute">
      Loading map…
    </div>
  ),
});

export default MapView;
