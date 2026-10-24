// Shapes of the backend's REST and /ws/live payloads (backend/app/api, backend/app/scoring/evaluator.py).

export type Tier = "CRITICAL" | "HIGH" | "WATCH" | "STABLE";
export type CodeStatus = "FULL" | "DNR" | "DNR-DNI" | "COMFORT";
export type Unit = "East" | "West" | "Memory Care";
export type FactorKind = "rule" | "vital" | "behavior" | "concern" | "overdue" | "device";

export type SimState = {
  mode: "live" | "replay";
  paused: boolean;
  force_night: boolean;
  display_clock_offset_s: number;
  force_night_since?: number | null;
  simulated_band?: boolean;
};

export type WireRule = {
  rule: string;
  kind: "critical" | "fall_prevention" | "device";
  text: string;
  since: string | null;
  held: boolean;
  ack: boolean;
};

export type WireFactor = { text: string; kind: FactorKind };

/** One resident in the compact /ws/live queue; rank = position in `items`. */
export type WireCard = {
  id: number;
  score: number;
  tier: Tier;
  rule: WireRule | null;
  device: string | null;
  factors: WireFactor[];
  last_check: string | null;
  check_min: number | null;
  suppressed: boolean;
  in_bed: boolean | null;
  worn: boolean | null;
  hr: number | null;
  rr: number | null;
  spo2: number | null;
  spark?: (number | null)[];
  /** The newest Snowflake Cortex headline (flagged cards only). */
  ai?: { text: string; at: string };
};

export type Kpis = {
  tiers: Record<Tier, number>;
  overdue_checks: number;
  devices_offline: number;
};

export type QueueMessage = {
  type: "queue";
  as_of: string;
  display_time: string | null;
  night: boolean | null;
  mode: "live" | "replay";
  eval_ms: number;
  kpis: Kpis;
  items: WireCard[];
};

export type AlertMessage = {
  type: "alert";
  ts: string;
  resident_id: number;
  name: string;
  room: string;
  unit: Unit;
  tier: Tier;
  from_tier: Tier;
  score: number;
  rule: { rule: string; kind: string; text: string; since: string | null } | null;
  new_rules: string[];
  factors: string[];
  sound: boolean;
};

export type EventType =
  | "bed_exit"
  | "bed_return"
  | "fall_suspected"
  | "check_logged"
  | "note_added"
  | "intake_logged"
  | "spot_check"
  | "device_offline"
  | "device_online"
  | "alert_raised"
  | "alert_acknowledged"
  | "alert_resolved";

export type ResidentEvent = {
  ts: string;
  type: EventType;
  staff_id: number | null;
  payload: Record<string, unknown> | null;
};

/** A check or note just logged from the UI. */
export type EventMessage = { type: "event"; ts: string; event: ResidentEvent & { resident_id: number } };

/** /ws/live: a live device reading (Polar band or iPhone bed sensor) just arrived on /ws/ingest. */
export type DeviceMessage = {
  type: "device";
  ts: string;
  resident_id: number;
  device: "polar" | "phone";
  reading_ts: string;
  hr?: number | null;
  worn?: boolean | null;
  in_bed?: boolean | null;
  state?: string;
  level?: number;
  bed_motion?: number;
  energy?: number;
};

/** GET /api/devices */
export type DeviceStatus = {
  kind: "polar" | "phone";
  resident_id: number;
  sockets: number;
  last_seen: string | null;
  age_s: number | null;
  online: boolean;
  readings: number;
  last: Record<string, unknown>;
};

export type BedCalibration = {
  quiet_energy: number;
  burst_jolt: number;
  tilt_sep_deg: number;
  tilt_noise_deg: number;
  empty_energy: number | null;
  still_energy: number | null;
  calibrated: boolean;
  warnings: string[];
  mode: "tilt" | "energy" | "burst";
};

export type DevicesResponse = {
  live_resident_id: number;
  public_url: string | null;
  bed_url: string | null;
  simulated_band: boolean;
  devices: DeviceStatus[];
  bed: Record<string, { in_bed: boolean; calibrating: string | null; calibration: BedCalibration }>;
  stats: { phase: string; rows_written: number; events_written: number; last_flush_ms: number | null; last_error: string | null };
};

export type InjectionMessage = {
  type: "injection";
  ts: string;
  kind: string;
  resident_id: number;
  start: string;
  end: string;
  minutes: number;
};

/** GET /api/residents row. */
export type RosterEntry = {
  resident_id: number;
  name: string;
  age: number;
  sex: "F" | "M";
  room: string;
  unit: Unit;
  diagnoses: string[];
  flags: string[];
  morse: number;
  mobility: "independent" | "1-assist" | "2-assist" | "bedbound";
  cognition: "intact" | "mild" | "moderate" | "severe";
  code_status: CodeStatus;
  is_live: boolean;
};

export type ComponentKey = "news" | "anom" | "trend" | "behav" | "concern" | "overdue";

export type Components = {
  p_news: number;
  p_anom: number;
  p_trend: number;
  p_behav: number;
  p_concern: number;
  p_overdue: number;
  multi_signal: boolean;
  anom_source: "snowflake" | "tiger_z";
  anom_as_of: string | null;
  news2_total: number;
  anomalous_metrics: string[];
  bed_exits: number;
  restlessness: number;
  still: number;
  minutes_since_check: number | null;
  /** Points each component adds to the score, and its maximum (100 × weight), from the backend. */
  points: Record<ComponentKey, number>;
  max_points: Record<ComponentKey, number>;
};

export type News2Param = { value: number | boolean; points: number };

export type News2 = {
  total: number;
  params: Partial<Record<"rr" | "spo2" | "hr" | "sbp" | "temp" | "consciousness", News2Param>>;
  missing: string[];
  spo2_scale: 1 | 2;
  forces_high: boolean;
};

export type DetailFactor = { key: string; text: string; points: number; kind: FactorKind };

/** The full (REST) queue card for one resident, with the score breakdown. */
export type CurrentScore = {
  rank: number;
  resident_id: number;
  score: number;
  tier: Tier;
  score_tier: Tier;
  rule: (WireRule & { acknowledged: boolean; score: number }) | null;
  device: { rule: string; text: string } | null;
  factors: DetailFactor[];
  suppressed_until: string | null;
  last_check_ts: string | null;
  minutes_since_check: number | null;
  in_bed: boolean | null;
  worn: boolean | null;
  latest: { hr: number | null; rr: number | null; spo2: number | null; ts: string | null };
  components: Components;
  news2: News2;
  concern: { concern_level: string; confusion_change: string; source: string; summary: string } | null;
};

export type BlockStats = { p05: number; p50: number; p95: number };
/** metric → 4-hour time-of-day block ("0"–"5") → percentiles. */
export type VitalBaselines = Partial<Record<string, Partial<Record<string, BlockStats>>>>;

export type Assessment = {
  headline: string;
  assessment: string;
  possible_concerns: string[];
  recommended_checks: string[];
  escalation: string;
  care_plan_citations: { section: string; quote: string }[];
  confidence: "low" | "medium" | "high";
  caveats: string;
  source: "template" | "cortex";
  model: string;
  /** The moment the assessment describes (live: when it was made; replay: the cursor). */
  as_of?: string;
  /** A Snowflake Cortex call for this resident is running. */
  pending?: boolean;
  /** Why the template is shown instead of Cortex (POST /api/explain only). */
  fallback_reason?: string;
  generated_at?: string;
  latency_ms?: number;
  via?: "inference_rest" | "sql_api";
};

/** /ws/live: a new Cortex assessment was stored. */
export type InsightMessage = {
  type: "insight";
  kind: "assessment";
  resident_id: number;
  as_of: string;
  live: boolean;
  headline: string;
  model: string;
};

export type ResidentDetail = RosterEntry & {
  medications: { name: string; dose: string; flags: string[] }[];
  emergency_contact: { name: string; phone: string; relation: string } | null;
  profile: Record<string, string | string[]> | null;
  as_of: string;
  baselines: {
    tod_block_now: number;
    vitals: VitalBaselines;
    night_bed_exits_p95: number | null;
    still_minutes_p95: number | null;
  };
  current: CurrentScore | null;
  assessment: Assessment;
  events: ResidentEvent[];
};

export type VitalMetric = "hr" | "rr" | "spo2" | "skin_temp" | "motion" | "in_bed";

export type VitalsResponse = {
  resident_id: number;
  from: string;
  to: string;
  res: "1m" | "15m" | "1h";
  ts: string[];
  series: Partial<Record<VitalMetric, (number | null)[]>>;
};

export type AnomalyMetricKey = "hr" | "rr" | "spo2" | "skin_temp" | "motion";

/** One metric of one closed 15-min bucket from Snowflake ANOMALY_DETECTION (cached in Tiger ai_insights). */
export type AnomalyMetric = {
  y: number | null;
  forecast: number | null;
  lower: number | null;
  upper: number | null;
  z: number | null;
  severity: number;
  is_anomaly: boolean;
};

export type AnomalyBucket = {
  as_of: string; // when the result became known (bucket end + sync delay)
  bucket: string; // bucket start
  metrics: Partial<Record<AnomalyMetricKey, AnomalyMetric>>;
  anomalous: AnomalyMetricKey[];
  p_anom: number;
};

export type AnomalyResponse = { resident_id: number; from: string; to: string; source: string; items: AnomalyBucket[] };

export type StaffMember = {
  staff_id: number;
  name: string;
  role: "RN" | "LPN" | "CNA";
  shift: "day" | "evening" | "night";
  unit: Unit | null;
  on_duty: boolean;
};

export type SimStatus = {
  sim: SimState;
  live_sim: { phase: string; residents: number; last_tick: string | null; rows_written: number } | null;
  injections: { kind: string; resident_id: number; start: string; end: string; minutes: number }[];
  evaluator: { phase: string; cycles: number; last_ms: number; avg_ms: number; alerts_raised: number } | null;
};
