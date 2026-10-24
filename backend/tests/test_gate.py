"""Alarm-fatigue controls (SPEC §7d): hysteresis, persistence, suppression after a check, rule hold."""

from datetime import timedelta

from app.scoring.evaluator import AlertGate, hysteresis_tier
from app.scoring.rules import RuleHit
from tests.scoring_helpers import NIGHT

FALL = RuleHit("fall", "critical", 100, "Suspected fall")
BED_EXIT = RuleHit("fall_prevention", "fall_prevention", 90, "Out of bed without staff")
UNRESPONSIVE = RuleHit("unresponsive", "critical", 100, "Possibly unresponsive")
SPO2 = RuleHit("spo2_low", "critical", 100, "SpO2 low")
T = NIGHT


def step(gate, score, *, at, rules=(), check=None, rid=1):
    return gate.update(rid, score, rules=list(rules), last_check_ts=check, as_of=at)


def test_hysteresis_enter_high_at_60_exit_at_50() -> None:
    assert hysteresis_tier(59.9, "WATCH") == "WATCH"
    assert hysteresis_tier(60, "WATCH") == "HIGH"
    assert hysteresis_tier(55, "HIGH") == "HIGH"
    assert hysteresis_tier(50, "HIGH") == "HIGH"
    assert hysteresis_tier(49.9, "HIGH") == "WATCH"
    assert hysteresis_tier(72, "CRITICAL") == "CRITICAL"
    assert hysteresis_tier(65, "CRITICAL") == "HIGH"
    assert hysteresis_tier(45, "CRITICAL") == "WATCH"
    assert hysteresis_tier(30, "WATCH") == "WATCH" and hysteresis_tier(24, "WATCH") == "STABLE"


def test_persistence_needs_two_consecutive_evaluations() -> None:
    g = AlertGate()
    d = step(g, 65, at=T)
    assert d.tier == "STABLE" and d.pending == "HIGH" and not d.escalated
    d = step(g, 66, at=T + timedelta(seconds=2))
    assert d.tier == "HIGH" and d.escalated


def test_a_single_spike_never_alerts() -> None:
    g = AlertGate()
    assert step(g, 85, at=T).tier == "STABLE"
    d = step(g, 20, at=T + timedelta(seconds=2))
    assert d.tier == "STABLE" and not d.escalated
    assert step(g, 85, at=T + timedelta(seconds=4)).tier == "STABLE"  # the count restarted


def test_persistence_escalates_to_the_lower_of_two_candidates() -> None:
    g = AlertGate()
    step(g, 65, at=T)
    assert step(g, 85, at=T + timedelta(seconds=2)).tier == "HIGH"


def test_critical_rule_escalates_immediately() -> None:
    g = AlertGate()
    d = step(g, 100, at=T, rules=[FALL])
    assert d.tier == "CRITICAL" and d.escalated and d.rule == FALL and not d.rule_held


def test_dropping_out_of_high_resolves() -> None:
    g = AlertGate()
    step(g, 70, at=T)
    step(g, 70, at=T + timedelta(seconds=2))
    d = step(g, 40, at=T + timedelta(seconds=4))
    assert d.tier == "WATCH" and d.resolved


def test_suppression_after_check_caps_at_watch_for_30_min() -> None:
    g = AlertGate()
    step(g, 70, at=T)
    assert step(g, 70, at=T + timedelta(seconds=2)).tier == "HIGH"
    check = T + timedelta(seconds=3)
    d = step(g, 70, at=T + timedelta(seconds=4), check=check)
    assert d.tier == "WATCH" and d.resolved and d.suppressed_until == check + timedelta(minutes=30)
    d = step(g, 74, at=check + timedelta(minutes=10), check=check)
    assert d.tier == "WATCH" and not d.escalated  # rose < 10 since the check
    # after 30 min the alert may come back (persistence still applies)
    later = check + timedelta(minutes=30, seconds=1)
    assert step(g, 70, at=later, check=check).tier == "WATCH"
    assert step(g, 70, at=later + timedelta(seconds=2), check=check).escalated


def test_suppression_breaks_on_a_rise_of_10() -> None:
    g = AlertGate()
    step(g, 62, at=T)
    step(g, 62, at=T + timedelta(seconds=2))
    check = T + timedelta(seconds=3)
    assert step(g, 62, at=T + timedelta(seconds=4), check=check).tier == "WATCH"
    assert step(g, 72, at=T + timedelta(minutes=5), check=check).tier == "WATCH"  # pending (persistence)
    d = step(g, 72, at=T + timedelta(minutes=5, seconds=2), check=check)
    assert d.tier == "HIGH" and d.escalated and d.suppressed_until is None


def test_rules_are_never_suppressed() -> None:
    g = AlertGate()
    check = T - timedelta(minutes=5)
    step(g, 20, at=T - timedelta(minutes=4), check=check)
    d = step(g, 100, at=T, rules=[FALL], check=check)
    assert d.tier == "CRITICAL" and d.escalated


def test_rule_is_held_5_min_unless_checked() -> None:
    g = AlertGate()
    step(g, 100, at=T, rules=[FALL])
    d = step(g, 40, at=T + timedelta(minutes=2))
    assert d.tier == "CRITICAL" and d.rule_held and d.score == 100 and d.rule == FALL
    d = step(g, 40, at=T + timedelta(minutes=5, seconds=1))
    assert d.tier == "WATCH" and d.rule is None and d.resolved

    g = AlertGate()
    step(g, 100, at=T, rules=[FALL])
    d = step(g, 40, at=T + timedelta(seconds=10), check=T + timedelta(seconds=8))
    assert d.tier == "WATCH" and d.rule is None


def test_seeded_tier_does_not_re_alert_after_restart() -> None:
    g = AlertGate()
    g.seed(1, "HIGH", 70)
    d = step(g, 70, at=T)
    assert d.tier == "HIGH" and not d.escalated


def test_stateless_gate_has_no_persistence() -> None:
    assert step(AlertGate(persistence=1), 65, at=T).tier == "HIGH"


def test_new_rule_alerts_even_when_already_critical() -> None:
    g = AlertGate()
    d = step(g, 90, at=T, rules=[BED_EXIT])
    assert d.tier == "CRITICAL" and d.alert and d.new_rules == ("fall_prevention",)
    d = step(g, 90, at=T + timedelta(seconds=2), rules=[BED_EXIT])
    assert not d.alert  # same episode, same rule
    d = step(g, 100, at=T + timedelta(seconds=60), rules=[FALL, BED_EXIT])
    assert d.tier == "CRITICAL" and not d.escalated and d.alert and d.new_rules == ("fall",)


def test_less_severe_rule_joining_does_not_alert() -> None:
    g = AlertGate()
    step(g, 100, at=T, rules=[FALL])
    d = step(g, 100, at=T + timedelta(seconds=2), rules=[FALL, BED_EXIT])
    assert not d.alert and d.new_rules == ()


def test_rule_on_score_critical_resident_alerts() -> None:
    g = AlertGate()
    step(g, 85, at=T)
    assert step(g, 85, at=T + timedelta(seconds=2)).tier == "CRITICAL"
    d = step(g, 100, at=T + timedelta(seconds=4), rules=[FALL])
    assert d.alert and not d.escalated


def test_check_acknowledges_a_rule_that_keeps_firing() -> None:
    g = AlertGate()
    assert step(g, 100, at=T, rules=[UNRESPONSIVE]).tier == "CRITICAL"
    check = T + timedelta(minutes=3)
    d = step(g, 100, at=check + timedelta(seconds=2), rules=[UNRESPONSIVE], check=check)
    assert d.tier == "WATCH" and d.rule_acknowledged and d.resolved and d.suppressed_until is not None
    d = step(g, 100, at=check + timedelta(minutes=10), rules=[UNRESPONSIVE], check=check)
    assert d.tier == "WATCH" and not d.alert
    # a new rule breaks through the acknowledgement at once
    d = step(g, 100, at=check + timedelta(minutes=11), rules=[UNRESPONSIVE, SPO2], check=check)
    assert d.tier == "CRITICAL" and d.alert and not d.rule_acknowledged


def test_acknowledged_rule_re_alerts_after_suppression() -> None:
    g = AlertGate()
    step(g, 100, at=T, rules=[UNRESPONSIVE])
    check = T + timedelta(minutes=1)
    step(g, 100, at=check + timedelta(seconds=2), rules=[UNRESPONSIVE], check=check)
    later = check + timedelta(minutes=30, seconds=1)
    assert step(g, 100, at=later, rules=[UNRESPONSIVE], check=check).tier == "WATCH"  # persistence
    d = step(g, 100, at=later + timedelta(seconds=2), rules=[UNRESPONSIVE], check=check)
    assert d.tier == "CRITICAL" and d.escalated


def test_check_before_a_new_rule_does_not_clear_its_hold() -> None:
    g = AlertGate()
    step(g, 100, at=T, rules=[UNRESPONSIVE])
    check = T + timedelta(minutes=10)
    step(g, 100, at=check + timedelta(seconds=2), rules=[UNRESPONSIVE], check=check)  # acknowledged
    step(g, 100, at=T + timedelta(minutes=20), rules=[SPO2], check=check)  # new rule after the check
    d = step(g, 40, at=T + timedelta(minutes=21), check=check)
    assert d.rule_held and d.rule == SPO2 and d.tier == "CRITICAL"


def test_seeded_rule_episode_does_not_re_alert() -> None:
    g = AlertGate()
    g.seed(1, "CRITICAL", 100, ["fall"], T - timedelta(seconds=10))
    d = step(g, 100, at=T, rules=[FALL])
    assert d.tier == "CRITICAL" and not d.alert
