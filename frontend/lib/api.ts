/** Thin API client. Every call goes through the FastAPI trust boundary. */

import { getToken } from "./token";
import type {
  ComplaintDetail,
  ComplaintListItem,
  ComplaintStatus,
  Hotspot,
  PublicConfig,
  SocialPost,
  AuthUser,
  DepartmentStats,
  DepartmentSummary,
  GeocodeResult,
  ReverseGeocodeResult,
  SessionResponse,
  Stats,
  SubmitResponse,
  SweepResult,
  Ward,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";



export function mediaUrl(path: string | null): string | null {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API_BASE}${path}`;
}

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init?.body && typeof init.body === "string") {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      `Cannot reach the API at ${API_BASE}. Is the backend running?`,
      0,
    );
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) {
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

/** Evidence Layer 1: bind the capture to a server-issued, single-use token. */
export function requestCaptureToken(payload: {
  device_id?: string;
  latitude?: number;
  longitude?: number;
  phone?: string;
}) {
  return request<{
    token: string;
    token_id: string;
    issued_at: string;
    expires_at: string;
    ttl_seconds: number;
  }>("/api/capture-token", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitComplaint(form: FormData) {
  return request<SubmitResponse>("/api/complaints", {
    method: "POST",
    body: form,
  });
}

export const getComplaintStatus = (reference: string) =>
  request<ComplaintStatus>(`/api/complaints/${encodeURIComponent(reference)}`);

export const getConfig = () => request<PublicConfig>("/api/config");

export const getStats = () => request<Stats>("/api/admin/stats");

export function listComplaints(params: Record<string, string | undefined>) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, value);
  }
  const suffix = query.toString() ? `?${query}` : "";
  return request<ComplaintListItem[]>(`/api/admin/complaints${suffix}`);
}

export const getComplaintDetail = (reference: string) =>
  request<ComplaintDetail>(
    `/api/admin/complaints/${encodeURIComponent(reference)}`,
  );

export const getHotspots = (windowHours = 720) =>
  request<Hotspot[]>(`/api/admin/hotspots?window_hours=${windowHours}`);

export const getWards = () => request<Ward[]>("/api/admin/wards");

export const getSocialPosts = () =>
  request<SocialPost[]>("/api/admin/social-posts");

export const runSlaSweep = () =>
  request<SweepResult>("/api/admin/sla/sweep", { method: "POST" });

export const resolveComplaint = (
  reference: string,
  body: { resolution_note: string; field_outcome: string },
) =>
  request<ComplaintDetail>(
    `/api/admin/complaints/${encodeURIComponent(reference)}/resolve`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const reviewComplaint = (
  reference: string,
  body: {
    approve: boolean;
    note: string;
    override_category?: string | null;
    override_priority?: string | null;
  },
) =>
  request<ComplaintDetail>(
    `/api/admin/complaints/${encodeURIComponent(reference)}/review`,
    { method: "POST", body: JSON.stringify(body) },
  );

export { ApiError };


// ------------------------------------------------------------------- auth
export const register = (body: {
  email: string;
  password: string;
  display_name: string;
  phone?: string;
}) =>
  request<SessionResponse>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const login = (body: { email: string; password: string }) =>
  request<SessionResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const fetchMe = () => request<AuthUser>("/api/auth/me");

export const changePassword = (body: {
  current_password: string;
  new_password: string;
}) =>
  request<SessionResponse>("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const myComplaints = () =>
  request<ComplaintListItem[]>("/api/me/complaints");

// ------------------------------------------------------------- department
export const getMyDepartment = () =>
  request<DepartmentSummary>("/api/department/me");

export const getDepartmentStats = () =>
  request<DepartmentStats>("/api/department/stats");

export function listDepartmentComplaints(
  params: Record<string, string | undefined>,
) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, value);
  }
  const suffix = query.toString() ? `?${query}` : "";
  return request<ComplaintListItem[]>(`/api/department/complaints${suffix}`);
}

export const getDepartmentComplaint = (reference: string) =>
  request<ComplaintDetail>(
    `/api/department/complaints/${encodeURIComponent(reference)}`,
  );

export const acknowledgeComplaint = (reference: string, note: string) =>
  request<ComplaintDetail>(
    `/api/department/complaints/${encodeURIComponent(reference)}/acknowledge`,
    { method: "POST", body: JSON.stringify({ note }) },
  );

export const departmentResolve = (
  reference: string,
  body: { resolution_note: string; field_outcome: string },
) =>
  request<ComplaintDetail>(
    `/api/department/complaints/${encodeURIComponent(reference)}/resolve`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const reassignComplaint = (
  reference: string,
  body: { target_department_code: string; reason: string },
) =>
  request<ComplaintDetail>(
    `/api/department/complaints/${encodeURIComponent(reference)}/reassign`,
    { method: "POST", body: JSON.stringify(body) },
  );

// --------------------------------------------------------------- geocoding
export const geocodeSearch = (q: string) =>
  request<GeocodeResult[]>(`/api/geocode/search?q=${encodeURIComponent(q)}`);

export const geocodeReverse = (lat: number, lon: number) =>
  request<ReverseGeocodeResult>(
    `/api/geocode/reverse?lat=${lat}&lon=${lon}`,
  );

export const geocodeDefaults = () =>
  request<{
    center: { latitude: number; longitude: number };
    bounds: {
      min_latitude: number;
      min_longitude: number;
      max_latitude: number;
      max_longitude: number;
    };
    city: string;
    attribution: string;
  }>("/api/geocode/defaults");
