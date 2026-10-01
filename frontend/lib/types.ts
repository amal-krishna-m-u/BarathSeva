/** Types mirroring the FastAPI response models in `backend/app/schemas.py`. */

export type Severity = "info" | "warn" | "fail";

export interface Signal {
  code: string;
  label: string;
  severity: Severity;
  detail: string;
  delta: number;
}

export interface Evidence {
  source: string;
  capture_token_valid: boolean;
  server_received_at: string;
  authenticity_score: number;
  outcome: string;
  hard_fail_reason: string | null;
  signals: Signal[];
  sha256: string | null;
  phash: string | null;
  media_path: string | null;
  image_width: number | null;
  image_height: number | null;
  exif_present: boolean;
  exif_datetime: string | null;
  exif_time_delta_seconds: number | null;
  exif_gps_distance_meters: number | null;
  editing_software: string | null;
  gps_accuracy_meters: number | null;
  mock_location_flag: boolean;
  inside_serviced_ward: boolean | null;
  duplicate_of_complaint_id: number | null;
  exact_duplicate: boolean;
}

export interface SubmitResponse {
  reference: string;
  accepted: boolean;
  status: string;
  message: string;
  authenticity_score: number;
  authenticity_outcome: string;
  evidence_signals: Signal[];
  rejection_reason: string | null;
  category: string | null;
  priority: string | null;
  ward_name: string | null;
  nearby_count: number;
  is_hotspot: boolean;
  department_code: string | null;
  external_ticket_id: string | null;
  sla_due_at: string | null;
  needs_human_review: boolean;
  pipeline_trace: string[];
}

export interface ComplaintEvent {
  id: number;
  event_type: string;
  actor: string;
  message: string | null;
  payload: Record<string, unknown>;
  created_at: string;
  entry_hash: string | null;
}

export interface AgentRun {
  id: number;
  agent_name: string;
  status: string;
  is_ai: boolean;
  provider: string | null;
  model: string | null;
  confidence: number | null;
  latency_ms: number | null;
  rationale: string | null;
  output: Record<string, unknown>;
  error: string | null;
  created_at: string;
}

export interface ComplaintStatus {
  reference: string;
  received: boolean;
  received_at: string;
  verified: boolean;
  verification_reason: string | null;
  status: string;
  category: string | null;
  priority: string | null;
  ward_name: string | null;
  department_code: string | null;
  department_name: string | null;
  external_ticket_id: string | null;
  sla_due_at: string | null;
  sla_breached: boolean;
  escalation_level: number;
  needs_human_review: boolean;
  is_hotspot: boolean;
  nearby_count: number;
  cluster_summary: string | null;
  resolved: boolean;
  resolved_at: string | null;
  resolution_message: string | null;
  rejection_reason: string | null;
  authenticity_score: number | null;
  authenticity_outcome: string | null;
  photo_url: string | null;
  events: ComplaintEvent[];
}

export interface ComplaintListItem {
  id: number;
  reference: string;
  description: string;
  status: string;
  category: string | null;
  priority: string | null;
  ward_name: string | null;
  department_code: string | null;
  external_ticket_id: string | null;
  latitude: number;
  longitude: number;
  nearby_count: number;
  is_hotspot: boolean;
  sla_due_at: string | null;
  sla_breached: boolean;
  escalation_level: number;
  needs_human_review: boolean;
  authenticity_score: number | null;
  authenticity_outcome: string | null;
  photo_url: string | null;
  created_at: string;
}

export interface ComplaintDetail extends ComplaintListItem {
  address_text: string | null;
  severity_note: string | null;
  verification_reason: string | null;
  rejection_reason: string | null;
  cluster_summary: string | null;
  resolution_note: string | null;
  resolution_message: string | null;
  resolved_at: string | null;
  field_outcome: string | null;
  reporter_name: string | null;
  reporter_trust: number | null;
  evidence: Evidence | null;
  events: ComplaintEvent[];
  agent_runs: AgentRun[];
  audit_chain_intact: boolean;
}

export interface Stats {
  total: number;
  by_status: Record<string, number>;
  by_category: Record<string, number>;
  by_priority: Record<string, number>;
  by_department: Record<string, number>;
  open_count: number;
  resolved_count: number;
  rejected_count: number;
  breached_count: number;
  pending_review_count: number;
  hotspot_count: number;
  ai_provider: string;
  ai_is_real_model: boolean;
}

export interface Hotspot {
  cluster_id: number;
  complaint_count: number;
  latitude: number;
  longitude: number;
  categories: string[];
  ward_name: string | null;
  open_count: number;
  breached_count: number;
}

export interface Ward {
  id: number;
  ward_number: string;
  name: string;
  zone: string;
  complaint_count: number;
  boundary: { type?: string; coordinates?: unknown };
}

export interface SocialPost {
  id: number;
  complaint_reference: string;
  kind: string;
  content: string;
  eligibility_reason: string | null;
  is_published: boolean;
  created_at: string;
}

export interface PublicConfig {
  city: string;
  capture_token_ttl_seconds: number;
  gps_accuracy_max_meters: number;
  authenticity_auto_accept: number;
  authenticity_review_floor: number;
  cluster_radius_meters: number;
  cluster_window_hours: number;
  hotspot_min_complaints: number;
  categories: string[];
  priorities: string[];
  statuses: string[];
}

export interface SweepResult {
  checked: number;
  newly_breached: string[];
  newly_escalated: string[];
  clusters_updated: number;
  swept_at: string;
}

export interface LiveEvent {
  id: number;
  reference: string;
  event_type: string;
  actor: string;
  message: string | null;
  status: string;
  created_at: string;
}
