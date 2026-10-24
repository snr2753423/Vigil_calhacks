// Shapes of the backend's REST and /ws/live payloads (backend/app/api, backend/app/scoring/evaluator.py).

export type Tier = "CRITICAL" | "HIGH" | "WATCH" | "STABLE";
export type CodeStatus = "FULL" | "DNR" | "DNR-DNI" | "COMFORT";
export type Unit = "East" | "West" | "Memory Care";
export type FactorKind = "rule" | "vital" | "behavior" | "concern" | "overdue" | "device" | "trend";

export type SimState = {
  mode: "live" | "replay";
  paused: boolean;
  force_night: boolean;
  display_clock_offset_s: number;
  force_night_since?: number | null;
  simulated_band?: boolean;
  /** While replaying: the cursor and playback (backend app/sim/replay.py). */
  replay?: ReplayBrief | null;
};

/** Replay cursor and playback, in heartbeats / `sim` messages and each `replay` step message. */
export type ReplayBrief = {
  cursor: string;
  speed: number;
  playing: boolean;
  step_s: number;
  start: string;
  end: string;
};

/** /ws/live: one replay step (or a seek / pause / speed change). */
export type ReplayMessage = ReplayBrief & { type: "replay"; ts: string; seeked: boolean };

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
  /** The Snowflake FORECAST behind P_trend: the metric, the NEWS2 3-point threshold it crosses, and when. */
  trend_metric?: "rr" | "spo2" | "hr" | null;
  trend_threshold?: number | null;
  trend_direction?: "up" | "down" | null;
  trend_crosses_at?: string | null;
  trend_as_of?: string | null;
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

/** /ws/live: a new Snowflake result was stored (Cortex assessment, forecast, note extraction). */
export type InsightMessage = {
  type: "insight";
  kind: "assessment" | "forecast" | "note_extract";
  resident_id: number;
  as_of: string;
  live: boolean;
  headline?: string;
  model?: string;
  callout?: string | null;
};

/** [bucket start ISO, forecast, lower, upper] (90 % interval). */
export type ForecastPoint = [string, number | null, number | null, number | null];

export type ForecastMetric = {
  points: ForecastPoint[];
  n_train: number;
  last_ts: string;
  last_value: number | null;
  crossing: { threshold: number; direction: "up" | "down"; bucket: string; crosses_at: string } | null;
};

/** One SNOWFLAKE.ML.FORECAST run (Tiger ai_insights, kind = forecast). */
export type Forecast = {
  as_of: string;
  data_through: string;
  train_hours: number;
  periods: number;
  chart_periods: number;
  interval: number;
  model: string;
  metrics: Partial<Record<"rr" | "spo2" | "hr", ForecastMetric>>;
  p_trend: number;
  callout: string | null;
  seconds: number;
};

export type ForecastResponse = { resident_id: number; kind: "forecast"; items: Forecast[] };

/** AI_EXTRACT fields for a nurse note (or the keyword fallback in the same shape). */
export type NoteExtract = {
  confusion_change: "new" | "worse" | "same" | "better" | "unknown";
  oral_intake: "normal" | "reduced" | "refused" | "unknown";
  pain_0_10: number | null;
  urinary_symptoms: string | null;
  fall_or_near_fall: boolean;
  breathing_concern: boolean;
  mood_behavior: string | null;
  fever_or_warm: boolean;
  concern_level: "none" | "low" | "med" | "high";
  ai_concern_level?: string;
  one_line_summary: string;
  source: "snowflake" | "keyword";
  model?: string;
  ms?: number;
};

export type NoteResponse = ResidentEvent & {
  resident_id: number;
  transcript: { text: string; audio_duration: number | null; stage_path: string; ms: number } | null;
  extract: NoteExtract;
  /** ok | pending (still running; arrives on /ws/live) | why the keyword fallback was used */
  extract_status: string;
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
  /** Scored at the replay cursor (read-only: no Cortex refresh, no checks or notes). */
  replay?: boolean;
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

// ------------------------------------------------------------------ replay (GET /api/replay)

export type Bookmark = { ts: string; resident_id: number; label: string };

export type PrepareProgress = {
  phase: "idle" | "detecting" | "notes" | "scoring" | "forecasting" | "assessing" | "done" | "error";
  started_at: string | null;
  finished_at: string | null;
  step: number;
  total: number;
  message: string;
  error: string | null;
  running: boolean;
};

export type PreparedSummary = {
  prepared_at: string;
  seconds: number;
  step_s: number;
  detection: { ran: boolean; reason?: string; results?: number; seconds?: number; resident_buckets?: number; flagged_buckets?: number };
  gated: { steps: number; avg_ms: number; snapshots: number; alerts: number };
  assessments_summary: { planned: number; cortex: number; cached: number; failed: number };
  forecasts: { status: string; requested: number; stored: number; cached?: number; failed?: number };
  notes?: { notes: number; extracted: number; cached: number; failed: number };
};

export type ReplayStatus = {
  active: boolean;
  cursor: string | null;
  speed: number;
  speeds: number[];
  playing: boolean;
  step_s: number;
  at_end: boolean;
  night: { date: string; start: string; end: string } | null;
  bookmarks: Bookmark[];
  prepared: PreparedSummary | null;
  cards: number;
  stats: { steps: number; last_ms: number; avg_ms: number; max_ms: number; behind: number; last_error: string | null };
  prepare: PrepareProgress;
};

// ------------------------------------------------------------------ /under-the-hood (GET /api/stats/*)

export type TigerStats = {
  ok: boolean;
  error?: string;
  query_ms: number;
  versions: Record<string, string>;
  ingest: {
    rows_last_60s: number;
    rows_per_s: number;
    by_source: { source: string; rows: number; newest: string }[];
    live_loop: { phase: string; rows_written: number } | null;
    devices: { rows_written: number; flushes: number; last_flush_ms: number | null } | null;
  };
  rows: Record<"vitals" | "events" | "priority_snapshots" | "vitals_1m" | "vitals_15m" | "vitals_1h", number> | null;
  compression: {
    chunks: number | null;
    compressed_chunks: number | null;
    before_bytes: number;
    after_bytes: number;
    ratio: number | null;
    hypertable_bytes: number | null;
  };
  chunks: { hypertable_name: string; chunks: number; compressed: number; oldest: string; newest: string }[];
  caggs: { view_name: string; materialized_only: boolean; materialization_hypertable_name: string }[];
  jobs: {
    job_id: number;
    proc_name: string;
    hypertable_name: string | null;
    schedule: string;
    last_run_status: string | null;
    last_successful_finish: string | null;
    next_start: string | null;
    total_runs: number | null;
    total_failures: number | null;
    last_run_ms: number | null;
  }[];
};

export type BenchResult = {
  name: string;
  title: string;
  results: Record<"raw" | "vitals_1h", { ms: number; rows: number; mean_hr: number | null }>;
  speedup: number | null;
  total_ms: number;
  sql: Record<string, string>;
};

export type SurfaceFeature = {
  feature: string;
  what: string;
  calls: number;
  errors: number;
  avg_ms: number | null;
  process_calls: number;
  process_avg_ms: number | null;
};

export type ApiSurface = {
  key: string;
  label: string;
  endpoint: string;
  features: SurfaceFeature[];
  calls: number;
  errors: number;
  process_calls: number;
};

export type SnowflakeStats = {
  configured: boolean;
  model: string;
  totals: { calls: number; errors: number };
  cumulative: { since: string | null; calls: number; errors: number } | null;
  surfaces: ApiSurface[] | null;
  sync: { phase: string; last_sync: string | null; last_ms: number | null };
  sync_state: Record<string, { watermark: string | null; rows_synced: number; updated_at: string }> | null;
  models: { train_from: string; train_to: string; trained_at: string; days: number } | null;
  last_detection: {
    last_run: string | null;
    last_range: string[] | null;
    last_seconds: number;
    last_seconds_by_metric: Record<string, number>;
    last_rows: number;
    last_flagged: number;
    residents?: number;
  } | null;
  assessments: { completed: number; avg_ms: number | null };
  rest: { inference_endpoint: string; last_error: string | null };
  inventory: {
    ms?: number;
    cached?: boolean;
    models?: { name: string; created_on: string }[];
    search_services?: { name: string; indexing_state?: string; serving_state?: string; source_data_num_rows?: string }[];
    counts?: Record<string, number | string | null>;
    errors: string[];
  } | null;
  tiger_error?: string;
};

export type OutcomeStats = {
  prepared: boolean;
  message?: string;
  error?: string;
  query_ms?: number;
  night?: { date: string; start: string; end: string };
  rules?: { merge_minutes: number; fixed: string[] };
  fixed?: {
    total: number;
    vital_signs: number;
    bed_exits: number;
    residents: number;
    by_kind: { kind: string; label: string; alarms: number }[];
  };
  vigil?: { total: number; residents: number; by_tier: Record<string, number>; rule_alerts: number; score_alerts: number };
  cast?: { resident_id: number; fixed: number; vigil: number }[];
  alvarez?: {
    vigil_watch: string | null;
    vigil_high: string | null;
    vigil_number_one: string | null;
    scheduled_round: string | null;
    round_check: string | null;
    first_fixed_vital: string | null;
    first_fixed_vital_kind: string | null;
    first_fixed_any: string | null;
    fixed_alarms: number;
    fixed_bed_exit_alarms: number;
    lead_vs_round_min: number | null;
    lead_vs_fixed_min: number | null;
    alerts: { ts: string; tier: Tier; from_tier: Tier; score: number; rank: number }[];
    notes: { ts: string; text: string }[];
  };
};

// --------------------------------------------------------------------------- shift handoff (AI_AGG)

export type Shift = "day" | "evening" | "night";

export type SbarLine = {
  resident_id: number;
  name: string;
  room: string;
  situation: string;
  background: string;
  assessment: string;
  recommendation: string;
  source?: "template";
};

export type Handoff = {
  shift: Shift;
  date: string;
  start: string;
  end: string;
  complete: boolean;
  source: string;
  residents_flagged: number;
  generated_at: string;
  flagged: { resident_id: number; name: string; room: string; peak_tier: Tier | null; end_tier: Tier | null }[];
  critical_events: { time: string; resident: string; text: string }[];
  watch_next_shift: { resident: string; reason: string; action: string }[];
  resolved: { resident: string; text: string }[];
  device_issues: { resident: string; text: string }[];
  follow_ups: string[];
  residents: SbarLine[];
  model: string;
  via?: string;
  fixes?: string[];
  fallback_reason?: string;
  ms: number;
  input_rows: string[];
  sbar_rows?: number;
};

export type HandoffResponse = { shift: Shift; date: string; handoff: Handoff | null };

// --------------------------------------------------------------------------- Ask the facility (Cortex Analyst)

export type AskColumn = { name: string; type: string };

export type AskChart = { type: "line" | "bar"; x: string; y: string; series?: string } | null;

export type AskResult = {
  question: string;
  interpretation: string;
  sql: string | null;
  verified_query: { name: string; question: string; verified_by: string } | null;
  suggestions: string[];
  warnings: string[];
  request_id: string | null;
  analyst_ms: number;
  exec_ms?: number;
  total_ms?: number;
  models: string[];
  columns: AskColumn[];
  rows: (string | number | boolean | null)[][];
  row_count: number;
  truncated: boolean;
  chart: AskChart;
  error: string | null;
  /** Set when Cortex Analyst was unreachable and this is the last real answer to the same question. */
  fallback?: { answered_at: string | null; reason: string };
};
