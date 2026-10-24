"""Fast-tier evaluator loop every 2 s, as_of-driven (SPEC §7a, §7d).

Each cycle reads all residents' inputs in one pipelined batch (`tiger.load_scoring_rows`), scores them
(`priority.compute`), then passes each score through the `AlertGate`, the alarm-fatigue controls:

* hysteresis: a tier is entered at its threshold and left only 10 points below it (HIGH: in 60, out 50)
* persistence: a score-driven escalation needs 2 consecutive evaluations; rule hits escalate at once
* suppression: for 30 min after a logged check the tier is held at most WATCH and no alert is raised,
  unless the score rises ≥ 10 above where it was at the check, or a new rule fires
* rule episodes: once a rule fires it stays in force until a check is logged or it has been clear for 5 min,
  so a reading hovering at a threshold doesn't flap CRITICAL on and off. A check logged after the episode's
  latest new rule acknowledges it: a rule that keeps firing is then suppressed like any score. A rule that
  joins the episode (e.g. a fall after an unassisted bed exit) alerts even if the tier is already CRITICAL.

The loop writes `priority_snapshots`, emits `alert_raised` / `alert_resolved` / `fall_suspected` events,
and publishes the queue (and new alerts) on /ws/live. `evaluate()` is also used statelessly by
`GET /api/queue?as_of=…`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.config import Settings, get_settings
from app.db import tiger
from app.scoring import priority
from app.scoring.baseline import Baselines
from app.scoring.features import Profile, ResidentFeatures, build_features
from app.scoring.priority import TIER_ENTER, TIER_RANK, TIERS, PriorityResult, Tier, tier_for_score
from app.scoring.rules import NightClock, RuleHit
from app.sim.physiology import STILL_MOTION_PER_MIN
from app.state import sim_state
from app.ws.hub import hub

log = logging.getLogger(__name__)

APP_NAME = "vigil-eval"
CONTEXT_TTL_S = 300  # reload roster + baselines this often (the baseline job runs hourly)
LOG_EVERY_CYCLES = 30
QUEUE_REFRESH_S = 10.0  # /ws/live: send the queue when a card changes, else at most this often
WIRE_SPARK_MIN = 24  # /ws/live: HR sparklines for every flagged card and at least this many top cards
SLOW_CYCLE_MS = 1000
ALERT_TIERS = ("HIGH", "CRITICAL")


# --------------------------------------------------------------------------- alarm-fatigue gate


HYSTERESIS = 10.0
PERSISTENCE = 2
SUPPRESS_FOR = timedelta(minutes=30)
SUPPRESS_BREAK_RISE = 10.0
SUPPRESSED_MAX_TIER: Tier = "WATCH"
RULE_HOLD = timedelta(minutes=5)


def hysteresis_tier(score: float, current: Tier) -> Tier:
    """Tier from the score, but a tier already held is kept until the score falls 10 below its threshold."""
    cand = tier_for_score(score)
    for t in reversed(TIERS[TIER_RANK[cand] + 1 : TIER_RANK[current] + 1]):
        if score >= TIER_ENTER[t] - HYSTERESIS:
            return t
    return cand


def _min_tier(a: Tier, b: Tier) -> Tier:
    return a if TIER_RANK[a] <= TIER_RANK[b] else b


@dataclass
class GateState:
    tier: Tier = "STABLE"
    pending_tier: Tier | None = None
    pending_count: int = 0
    last_score: float | None = None
    check_ts: datetime | None = None
    score_at_check: float | None = None
    rule: RuleHit | None = None  # the rule episode in force (possibly held)
    rule_since: datetime | None = None
    rule_last_seen: datetime | None = None
    episode_rules: set[str] = field(default_factory=set)  # every rule that has fired in this episode
    rule_new_at: datetime | None = None  # when the latest new rule joined the episode

    def end_episode(self) -> None:
        self.rule = self.rule_since = self.rule_last_seen = self.rule_new_at = None
        self.episode_rules = set()

    def acknowledged(self) -> bool:
        """A check was logged after the episode's latest new rule."""
        return self.check_ts is not None and self.rule_new_at is not None and self.check_ts >= self.rule_new_at


@dataclass(frozen=True)
class GateDecision:
    tier: Tier
    prev_tier: Tier
    score: float  # the score, raised to a held rule's score while the hold lasts
    suppressed_until: datetime | None = None
    pending: Tier | None = None
    rule: RuleHit | None = None  # rule in force: firing now or held
    rule_held: bool = False
    rule_acknowledged: bool = False  # a check was logged since the rule fired: suppressed like a score
    new_rules: tuple[str, ...] = ()  # rules that joined the episode this evaluation

    @property
    def escalated(self) -> bool:
        return TIER_RANK[self.tier] > TIER_RANK[self.prev_tier] and self.tier in ALERT_TIERS

    @property
    def alert(self) -> bool:
        """Raise an alert: the tier went up into HIGH/CRITICAL, or a new rule fired."""
        return self.escalated or (bool(self.new_rules) and self.tier in ALERT_TIERS)

    @property
    def resolved(self) -> bool:
        return self.prev_tier in ALERT_TIERS and self.tier not in ALERT_TIERS


class AlertGate:
    def __init__(self, persistence: int = PERSISTENCE):
        self.persistence = persistence
        self.states: dict[int, GateState] = {}

    def seed(
        self, rid: int, tier: Tier, score: float | None = None, rules: Iterable[str] = (), at: datetime | None = None
    ) -> None:
        """Start from a known tier and rule episode (e.g. the last snapshot) so a restart doesn't re-raise
        every alert."""
        st = GateState(tier=tier, last_score=score, episode_rules=set(rules))
        if st.episode_rules:
            st.rule_since = st.rule_new_at = at
        self.states[rid] = st

    def update(
        self,
        rid: int,
        score: float,
        *,
        rules: list[RuleHit],
        last_check_ts: datetime | None,
        as_of: datetime,
        score_at_check: float | None = None,
    ) -> GateDecision:
        st = self.states.setdefault(rid, GateState())
        prev = st.tier

        if last_check_ts is not None and last_check_ts != st.check_ts:
            st.check_ts = last_check_ts
            if score_at_check is None:
                score_at_check = st.last_score if st.last_score is not None else score
            st.score_at_check = score_at_check

        held = False
        new_rules: tuple[str, ...] = ()
        if rules:
            st.rule_since = st.rule_since or as_of
            fresh = [h for h in rules if h.rule not in st.episode_rules]
            if fresh:
                st.episode_rules.update(h.rule for h in fresh)
                st.rule_new_at = as_of
                # alert only for a rule at least as severe as the one already in force (a fall after a bed
                # exit alerts; a bed-exit rule joining a fall doesn't)
                floor = st.rule.score if st.rule is not None else 0
                new_rules = tuple(dict.fromkeys(h.rule for h in fresh if h.score >= floor))
            st.rule, st.rule_last_seen = rules[0], as_of
        elif st.rule is not None and not st.acknowledged() and as_of - st.rule_last_seen < RULE_HOLD:
            held = True
            score = max(score, float(st.rule.score))
        elif st.rule is not None or st.episode_rules:
            st.end_episode()
        acknowledged = st.rule is not None and st.acknowledged()
        rule_active = st.rule is not None and not acknowledged

        cand = hysteresis_tier(score, st.tier)
        suppressed_until = None
        if (
            st.check_ts is not None
            and timedelta(0) <= as_of - st.check_ts < SUPPRESS_FOR
            and not rule_active
            and score < (st.score_at_check or 0.0) + SUPPRESS_BREAK_RISE
        ):
            suppressed_until = st.check_ts + SUPPRESS_FOR
            cand = _min_tier(cand, SUPPRESSED_MAX_TIER)

        if TIER_RANK[cand] > TIER_RANK[st.tier] and not rule_active:
            st.pending_count += 1
            st.pending_tier = cand if st.pending_tier is None else _min_tier(st.pending_tier, cand)
            if st.pending_count >= self.persistence:
                new = st.pending_tier
                st.pending_tier, st.pending_count = None, 0
            else:
                new = st.tier
        else:
            new = cand
            st.pending_tier, st.pending_count = None, 0

        st.tier = new
        st.last_score = score
        return GateDecision(new, prev, score, suppressed_until, st.pending_tier, st.rule, held, acknowledged, new_rules)


# --------------------------------------------------------------------------- one evaluation


@dataclass
class ScoringContext:
    profiles: dict[int, Profile]
    baselines: Baselines
    loaded_at: float = field(default_factory=time.monotonic)

    @property
    def stale(self) -> bool:
        return time.monotonic() - self.loaded_at > CONTEXT_TTL_S


async def load_context(conn: psycopg.AsyncConnection) -> ScoringContext:
    profiles = {
        r["resident_id"]: Profile(
            resident_id=r["resident_id"],
            name=r["name"],
            sex=r["sex"],
            room=r["room"],
            unit=r["unit"],
            mobility=r["mobility"],
            morse=r["morse"],
            cognition=r["cognition"],
            code_status=r["code_status"],
            flags=tuple(r["flags"] or ()),
            is_live=r["is_live"],
        )
        for r in await tiger.load_profiles(conn)
    }
    return ScoringContext(profiles, Baselines(await tiger.load_baseline_rows(conn)))


def night_clock(tz: ZoneInfo, *, live: bool = True) -> NightClock:
    """The night-window clock. The demo's force-night flag and display offset (SPEC §4) apply only to live
    evaluation; a historical as_of (replay, `/api/queue?as_of=`) is judged by the real facility clock."""
    if not live:
        return NightClock(tz=tz)
    since = sim_state.force_night_since
    return NightClock(
        tz=tz,
        force_night=sim_state.force_night,
        force_night_since=datetime.fromtimestamp(since, UTC) if since else None,
        offset_s=sim_state.display_clock_offset_s,
    )


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _iso_s(ts: datetime | None) -> str | None:
    """Whole-second UTC ISO 8601 ('…Z'), for the compact /ws/live payload."""
    return ts.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z") if ts else None


@dataclass
class QueueItem:
    profile: Profile
    features: ResidentFeatures
    result: PriorityResult
    decision: GateDecision
    clock: NightClock

    @property
    def tier(self) -> Tier:
        return self.decision.tier

    def sort_key(self) -> tuple:
        return (-TIER_RANK[self.tier], -self.decision.score, self.profile.resident_id)

    def to_dict(self, rank: int, *, detail: bool = True) -> dict:
        """The REST queue card. detail=False leaves out the score breakdown (components, NEWS2, note concern).
        /ws/live sends the smaller `to_wire` card instead."""
        p, f, r, d = self.profile, self.features, self.result, self.decision
        c = r.components
        card = {
            "rank": rank,
            "resident_id": p.resident_id,
            "name": p.name,
            "room": p.room,
            "unit": p.unit,
            "code_status": p.code_status,
            "is_live": p.is_live,
            "score": d.score,
            "tier": d.tier,
            "rule": (d.rule.to_dict() | {"held": d.rule_held, "acknowledged": d.rule_acknowledged}) if d.rule else None,
            "device": r.device.to_dict() if r.device else None,
            "factors": [x.to_dict() for x in r.factors],
            "suppressed_until": _iso(d.suppressed_until),
            "last_check_ts": _iso(f.last_check_ts),
            "minutes_since_check": None if c.minutes_since_check is None else round(c.minutes_since_check, 1),
            "in_bed": f.in_bed,
            "worn": f.worn,
            "latest": {
                "hr": None if f.hr_5m is None else round(f.hr_5m),
                "rr": None if f.rr_5m is None else round(f.rr_5m, 1),
                "spo2": f.spo2_recent[0] if f.spo2_recent else None,
                "ts": _iso(f.last_ts),
            },
            "hr_spark": [None if v is None else round(v, 1) for v in f.hr_spark],
        }
        if detail:
            card |= {
                "score_tier": r.tier,
                "components": c.to_dict(),
                "news2": r.news2.to_dict(),
                "concern": None if r.concern is None else asdict(r.concern),
            }
        return card

    def to_wire(self, *, spark: bool) -> dict:
        """The compact /ws/live card: only what the Rounds Queue and floor grid draw. Profile fields (name,
        room, unit, code status) come from GET /api/residents once; the score breakdown from the resident
        page's GET /api/residents/{id}. Rank is the position in `items`. STABLE cards carry only their top
        factor (the queue lists them as one-line rows)."""
        p, f, r, d = self.profile, self.features, self.result, self.decision
        msc = r.components.minutes_since_check
        card = {
            "id": p.resident_id,
            "score": round(d.score),
            "tier": d.tier,
            "rule": None
            if d.rule is None
            else {
                "rule": d.rule.rule,
                "kind": d.rule.kind,
                "text": d.rule.text,
                "since": _iso_s(d.rule.since),
                "held": d.rule_held,
                "ack": d.rule_acknowledged,
            },  # fmt: skip
            "device": r.device.text if r.device else None,
            "factors": [{"text": x.text, "kind": x.kind} for x in r.factors[: 3 if d.tier != "STABLE" else 1]],
            "last_check": _iso_s(f.last_check_ts),
            "check_min": None if msc is None else round(msc),
            "suppressed": d.suppressed_until is not None,
            "in_bed": f.in_bed,
            "worn": f.worn,
            "hr": None if f.hr_5m is None else round(f.hr_5m),
            "rr": None if f.rr_5m is None else round(f.rr_5m),
            "spo2": None if not f.spo2_recent else round(f.spo2_recent[0]),
        }
        if spark:
            card["spark"] = [None if v is None else round(v) for v in f.hr_spark]
        return card

    def signature(self) -> tuple:
        """What a nurse acts on: who is flagged, at what tier, for which rule. Scores, factor wording and
        vitals drift every cycle and are refreshed on a timer instead."""
        d = self.decision
        return (self.profile.resident_id, d.tier, d.rule.rule if d.rule else None, d.rule_acknowledged,
                d.suppressed_until)  # fmt: skip


@dataclass
class Queue:
    as_of: datetime
    items: list[QueueItem]
    query_ms: float = 0.0
    total_ms: float = 0.0
    gated: bool = True  # False: one stateless evaluation (no hysteresis/persistence history)

    def kpis(self) -> dict:
        tiers = {t: 0 for t in reversed(TIERS)}
        for it in self.items:
            tiers[it.tier] += 1
        overdue = sum(1 for it in self.items if (it.result.components.minutes_since_check or 1e9) >= 120)
        devices = sum(1 for it in self.items if it.result.device is not None)
        return {"tiers": tiers, "overdue_checks": overdue, "devices_offline": devices}

    def signature(self) -> tuple:
        """Ranked non-STABLE cards (see QueueItem.signature): a change here is pushed to /ws/live at once."""
        return tuple(it.signature() for it in self.items if it.tier != "STABLE")

    def to_wire(self, clock: NightClock | None = None) -> dict:
        """The /ws/live `queue` message (~34 KB for 120 residents, ~4 KB deflated; the slim REST cards are ~80 KB)."""
        clock = clock or (self.items[0].clock if self.items else None)
        return {
            "type": "queue",
            "as_of": _iso_s(self.as_of),
            "display_time": clock.display(self.as_of).replace(microsecond=0).isoformat() if clock else None,
            "night": clock.is_night(self.as_of) if clock else None,
            "mode": sim_state.mode,
            "eval_ms": round(self.total_ms),
            "kpis": self.kpis(),
            "items": [it.to_wire(spark=i < WIRE_SPARK_MIN or it.tier != "STABLE") for i, it in enumerate(self.items)],
        }

    def to_dict(self, clock: NightClock | None = None, *, detail: bool = True) -> dict:
        clock = clock or (self.items[0].clock if self.items else None)
        return {
            "type": "queue",
            "as_of": self.as_of.isoformat(),
            "display_time": clock.display(self.as_of).isoformat() if clock else None,
            "night": clock.is_night(self.as_of) if clock else None,
            "mode": sim_state.mode,
            "gated": self.gated,
            "eval_ms": round(self.total_ms, 1),
            "kpis": self.kpis(),
            "items": [it.to_dict(i + 1, detail=detail) for i, it in enumerate(self.items)],
        }


async def evaluate(
    conn: psycopg.AsyncConnection,
    as_of: datetime,
    ctx: ScoringContext,
    gate: AlertGate,
    clock: NightClock,
    *,
    check_scores: dict[int, float] | None = None,
) -> Queue:
    t0 = time.perf_counter()
    rows = await tiger.load_scoring_rows(conn, as_of, clock.night_start(as_of), STILL_MOTION_PER_MIN)
    t1 = time.perf_counter()
    feats = build_features(as_of, rows, ctx.profiles)
    items = []
    for rid, p in ctx.profiles.items():
        f = feats[rid]
        res = priority.compute(p, f, ctx.baselines, clock)
        dec = gate.update(
            rid,
            res.score,
            rules=res.rules,
            last_check_ts=f.last_check_ts,
            as_of=as_of,
            score_at_check=(check_scores or {}).get(rid),
        )
        items.append(QueueItem(p, f, res, dec, clock))
    items.sort(key=QueueItem.sort_key)
    t2 = time.perf_counter()
    return Queue(as_of, items, (t1 - t0) * 1000, (t2 - t0) * 1000)


async def evaluate_at(
    conn: psycopg.AsyncConnection, as_of: datetime, tz: ZoneInfo, ctx: ScoringContext | None, *, live: bool = False
) -> Queue:
    """Stateless evaluation for an arbitrary as_of (API): no hysteresis/persistence history; suppression uses
    the score recorded in priority_snapshots when the check was logged (else the score now). `live` = as_of is
    now, so the demo's force-night / display offset apply."""
    ctx = ctx or await load_context(conn)
    clock = night_clock(tz, live=live)
    checks = {
        r["resident_id"]: r["last_check"]
        for r in await tiger.fetch_dicts(
            conn,
            """SELECT resident_id, max(ts) AS last_check FROM events
               WHERE type = 'check_logged' AND ts > %(as_of)s - INTERVAL '30 minutes' AND ts <= %(as_of)s
               GROUP BY resident_id""",
            {"as_of": as_of},
        )
    }
    check_scores = await tiger.snapshot_scores_before(conn, checks)
    queue = await evaluate(conn, as_of, ctx, AlertGate(persistence=1), clock, check_scores=check_scores)
    queue.gated = False
    return queue


# --------------------------------------------------------------------------- live loop


@dataclass
class EvalStats:
    phase: str = "starting"  # starting | running | paused | error | disabled
    cycles: int = 0
    last_as_of: str | None = None
    last_ms: float = 0.0
    avg_ms: float = 0.0
    max_ms: float = 0.0
    slow_cycles: int = 0
    alerts_raised: int = 0
    snapshots_written: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Evaluator:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.tz = ZoneInfo(self.settings.facility_tz)
        self.interval_s = self.settings.eval_interval_s
        self.gate = AlertGate()
        self.ctx: ScoringContext | None = None
        self.latest: Queue | None = None
        self.stats = EvalStats()
        self._last_snapshot = 0.0
        self._fall_events: set[tuple[int, datetime]] = set()
        self._window: list[float] = []
        self._sent_sig: tuple | None = None
        self._sent_at = 0.0
        self._wake = asyncio.Event()

    def poke(self) -> None:
        """Run the next cycle now (e.g. right after a check is logged)."""
        self._wake.set()

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with tiger.connect(application_name=APP_NAME) as conn:
                    await self._prepare(conn)
                    backoff = 1.0
                    await self._loop(conn)
            except asyncio.CancelledError:
                raise
            except tiger.TigerNotConfigured:
                self.stats.phase = "disabled"
                log.warning("evaluator: TIGER_DATABASE_URL not set — evaluator disabled")
                return
            except Exception as exc:
                if tiger.cancelling():
                    raise asyncio.CancelledError from exc
                self.stats.phase = "error"
                self.stats.last_error = " ".join(str(exc).split())[:200]
                log.warning("evaluator: %s — retrying in %.0f s", self.stats.last_error, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _prepare(self, conn: psycopg.AsyncConnection) -> None:
        self.ctx = await load_context(conn)
        now = datetime.now(UTC)
        for r in await tiger.latest_snapshots(conn, now):
            if r["resident_id"] not in self.gate.states:
                keys = (x.get("key", "") for x in r["factors"] or ())
                rules = [k.removeprefix("rule:") for k in keys if k.startswith("rule:")]
                self.gate.seed(r["resident_id"], r["tier"], r["score"], rules, r["ts"])
        for rid, payload in await tiger.fetchall(
            conn,
            "SELECT resident_id, payload FROM events WHERE type = 'fall_suspected' AND ts > now() - INTERVAL '3 hours'",
        ):
            if ts := payload.get("impact_ts"):
                self._fall_events.add((rid, datetime.fromisoformat(ts)))

    async def _loop(self, conn: psycopg.AsyncConnection) -> None:
        while True:
            started = time.monotonic()
            if sim_state.paused:
                self.stats.phase = "paused"  # the simulator is frozen: freeze the queue with it
            else:
                await self.cycle(conn)
            self._wake.clear()
            delay = max(0.05, self.interval_s - (time.monotonic() - started))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), delay)

    async def cycle(self, conn: psycopg.AsyncConnection) -> Queue:
        if self.ctx is None or self.ctx.stale:
            self.ctx = await load_context(conn)
        now = datetime.now(UTC)
        as_of = now.replace(microsecond=now.microsecond // 1000 * 1000)
        clock = night_clock(self.tz)
        t0 = time.perf_counter()
        queue = await evaluate(conn, as_of, self.ctx, self.gate, clock)
        alerts, events = self._alerts(queue, clock)
        await self._write(conn, queue, events)
        total = (time.perf_counter() - t0) * 1000
        queue.total_ms = total
        self.latest = queue
        self._record(queue, total)
        await self._publish(queue, clock)
        for a in alerts:
            await hub.broadcast(a)
        return queue

    async def _publish(self, queue: Queue, clock: NightClock) -> None:
        """Push the compact queue on /ws/live right away when a flagged card changed (who, tier, rule, order),
        otherwise refresh it every QUEUE_REFRESH_S (scores, factors, vitals, minutes since check)."""
        sig, now = queue.signature(), time.monotonic()
        changed = sig != self._sent_sig or now - self._sent_at >= QUEUE_REFRESH_S
        await hub.publish_queue(queue.to_wire(clock), broadcast=changed)
        if changed:
            self._sent_sig, self._sent_at = sig, now

    def _alerts(self, queue: Queue, clock: NightClock) -> tuple[list[dict], list[tuple]]:
        ts = queue.as_of.timestamp()
        alerts, events = [], []
        for it in queue.items:
            p, r, d = it.profile, it.result, it.decision
            factors = [x.text for x in r.factors]
            if d.alert:
                payload = {"tier": d.tier, "from_tier": d.prev_tier, "score": d.score,
                           "rule": d.rule.rule if d.rule else None, "new_rules": list(d.new_rules),
                           "factors": factors}  # fmt: skip
                events.append((ts, p.resident_id, "alert_raised", None, payload))
                alerts.append({
                    "type": "alert", "ts": queue.as_of.isoformat(), "resident_id": p.resident_id, "name": p.name,
                    "room": p.room, "unit": p.unit, "tier": d.tier, "from_tier": d.prev_tier, "score": d.score,
                    "rule": d.rule.to_dict() if d.rule else None, "new_rules": list(d.new_rules),
                    "factors": factors, "sound": d.tier == "CRITICAL",
                })  # fmt: skip
            elif d.resolved:
                reason = "checked" if d.suppressed_until else "improved"
                payload = {"tier": d.tier, "from_tier": d.prev_tier, "score": d.score, "reason": reason}
                events.append((ts, p.resident_id, "alert_resolved", None, payload))
            fall = next((h for h in r.rules if h.rule == "fall"), None)
            if fall is not None and (key := (p.resident_id, it.features.impact_ts)) not in self._fall_events:
                self._fall_events.add(key)
                payload = {"impact_ts": it.features.impact_ts.isoformat(), "impact_g": it.features.impact_g,
                           "text": fall.text}  # fmt: skip
                events.append((ts, p.resident_id, "fall_suspected", None, payload))
        return alerts, events

    async def _write(self, conn: psycopg.AsyncConnection, queue: Queue, events: list[tuple]) -> None:
        now = time.monotonic()
        full = now - self._last_snapshot >= self.settings.snapshot_interval_s
        items = [it for it in queue.items if full or it.decision.tier != it.decision.prev_tier]
        if full:
            self._last_snapshot = now
        if items:
            rows = (
                (queue.as_of, it.profile.resident_id, it.decision.score, it.tier,
                 it.result.components.to_dict(), [x.to_dict() for x in it.result.factors])
                for it in items
            )  # fmt: skip
            self.stats.snapshots_written += await tiger.write_snapshots(conn, rows)
        if events:
            await tiger.write_events(conn, events)
            self.stats.alerts_raised += sum(1 for e in events if e[2] == "alert_raised")

    def _record(self, queue: Queue, total_ms: float) -> None:
        s = self.stats
        s.phase, s.cycles, s.last_as_of, s.last_ms = (
            "running",
            s.cycles + 1,
            queue.as_of.isoformat(),
            round(total_ms, 1),
        )
        s.max_ms = round(max(s.max_ms, total_ms), 1)
        self._window.append(total_ms)
        if total_ms > SLOW_CYCLE_MS:
            s.slow_cycles += 1
            log.warning("evaluator: slow cycle %.0f ms (queries %.0f ms)", total_ms, queue.query_ms)
        if len(self._window) >= LOG_EVERY_CYCLES:
            s.avg_ms = round(sum(self._window) / len(self._window), 1)
            top = queue.items[0] if queue.items else None
            log.info(
                "evaluator: %d cycles avg %.0f ms, max %.0f ms (last: queries %.0f ms) · %s · top: %s",
                len(self._window), s.avg_ms, max(self._window), queue.query_ms,
                " ".join(f"{k[0]}={v}" for k, v in queue.kpis()["tiers"].items()),
                f"{top.profile.name} {top.tier} {top.decision.score:.0f}" if top else "-",
            )  # fmt: skip
            self._window.clear()


evaluator: Evaluator | None = None


def start() -> asyncio.Task | None:
    global evaluator
    settings = get_settings()
    if not (settings.evaluator_enabled and settings.tiger_configured):
        return None
    evaluator = Evaluator(settings)
    return asyncio.create_task(evaluator.run(), name="evaluator")
