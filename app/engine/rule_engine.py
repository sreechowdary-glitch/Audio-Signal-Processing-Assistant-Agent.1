"""
rule_engine.py
--------------
Rule evaluator: iterates all 38 RuleDefinitions against CircuitCalculations
and returns a structured RuleEvaluationResult.

Responsibilities
----------------
- Accept a raw circuit parameter dict from the Flask route.
- Call circuit_calculator.compute_all() to produce CircuitCalculations.
- Evaluate every rule in RULES in order; collect triggered rules.
- Sort triggered rules by severity (critical first).
- Derive a primary_issue and consolidated root_cause string.
- Return RuleEvaluationResult — consumed by Circuit Reliability Assessment Engine, API route, PDF generator.

No AI calls. No I/O. Pure deterministic logic.

Dependencies: circuit_calculator, rules_library (internal)
"""

import logging
from dataclasses import dataclass, field

from app.engine.circuit_calculator import CircuitCalculations, compute_all
from app.engine.rules_library import RULES, RuleDefinition

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Severity ordering for sorting
# ---------------------------------------------------------------------------
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


# ---------------------------------------------------------------------------
# Triggered rule result
# ---------------------------------------------------------------------------
@dataclass
class TriggeredRule:
    """
    Represents one rule that fired during evaluation.

    Attributes
    ----------
    rule_id            : e.g. 'R01'
    category           : fault category string
    severity           : 'critical' | 'high' | 'medium' | 'low' | 'info'
    fault_name         : short fault label
    root_cause         : engineering root-cause explanation
    corrective_actions : list of fix strings
    engineering_detail : numeric context string (computed from CircuitCalculations)
    """
    rule_id:             str
    category:            str
    severity:            str
    fault_name:          str
    root_cause:          str
    corrective_actions:  list[str]
    engineering_detail:  str


# ---------------------------------------------------------------------------
# Top-level result dataclass
# ---------------------------------------------------------------------------
@dataclass
class RuleEvaluationResult:
    """
    Full output of the rule engine for one circuit diagnostic session.

    Attributes
    ----------
    calculations       : all computed CircuitCalculations values
    triggered_rules    : list of TriggeredRule, sorted critical-first
    triggered_count    : total number of triggered rules
    critical_count     : number of critical-severity triggered rules
    high_count         : number of high-severity triggered rules
    primary_issue      : headline fault string (worst triggered rule)
    root_cause         : consolidated root cause (from worst rule)
    corrective_actions : de-duplicated list from all triggered rules
    categories_affected: set of affected fault categories
    """
    calculations:        CircuitCalculations
    triggered_rules:     list[TriggeredRule]
    triggered_count:     int
    critical_count:      int
    high_count:          int
    primary_issue:       str
    root_cause:          str
    corrective_actions:  list[str]
    categories_affected: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def evaluate(params: dict) -> RuleEvaluationResult:
    """
    Run all 38 engineering rules against the supplied circuit parameters.

    Parameters
    ----------
    params : dict — see circuit_calculator.compute_all() for key specification

    Returns
    -------
    RuleEvaluationResult
    """
    logger.info(
        "Rule engine: evaluating %d rules for circuit_type=%s, op_amp=%s, "
        "gain=%.1f, Vs=%.1fV, Vin=%.1f mV",
        len(RULES),
        params.get("circuit_type", "?"),
        params.get("op_amp_model", "?"),
        float(params.get("gain", 0)),
        float(params.get("supply_voltage_v", 0)),
        float(params.get("input_signal_mv", 0)),
    )

    # -- 1. Compute all engineering quantities --------------------------------
    calc = compute_all(params)

    # -- 2. Evaluate each rule -----------------------------------------------
    triggered: list[TriggeredRule] = []

    for rule in RULES:
        try:
            fired = rule.condition(calc)
        except Exception as exc:
            # A rule condition that raises should never crash the engine
            logger.warning("Rule %s condition raised an exception: %s", rule.rule_id, exc)
            fired = False

        if fired:
            try:
                detail = rule.engineering_detail(calc)
            except Exception as exc:
                logger.warning("Rule %s detail function raised: %s", rule.rule_id, exc)
                detail = ""

            triggered.append(TriggeredRule(
                rule_id=rule.rule_id,
                category=rule.category,
                severity=rule.severity,
                fault_name=rule.fault_name,
                root_cause=rule.root_cause,
                corrective_actions=rule.corrective_actions,
                engineering_detail=detail,
            ))

    # -- 3. Sort by severity -------------------------------------------------
    triggered.sort(key=lambda r: _SEVERITY_ORDER.get(r.severity, 99))

    # -- 4. Counts -----------------------------------------------------------
    critical_count = sum(1 for r in triggered if r.severity == "critical")
    high_count     = sum(1 for r in triggered if r.severity == "high")

    # -- 5. Primary issue & root cause (from worst rule) ---------------------
    if triggered:
        worst         = triggered[0]
        primary_issue = worst.fault_name
        root_cause    = worst.root_cause
    else:
        primary_issue = "No engineering violations detected"
        root_cause    = (
            "All rule checks passed. Circuit parameters are within acceptable "
            "operating limits for the selected op-amp and supply voltage."
        )

    # -- 6. Consolidated corrective actions (preserve order, deduplicate) ----
    seen_actions: set[str] = set()
    all_actions:  list[str] = []
    for rule in triggered:
        for action in rule.corrective_actions:
            if action not in seen_actions:
                seen_actions.add(action)
                all_actions.append(action)

    # -- 7. Affected categories ----------------------------------------------
    categories: list[str] = []
    for rule in triggered:
        if rule.category not in categories:
            categories.append(rule.category)

    logger.info(
        "Rule evaluation complete: %d triggered (%d critical, %d high). "
        "Primary issue: '%s'",
        len(triggered), critical_count, high_count, primary_issue,
    )

    return RuleEvaluationResult(
        calculations=calc,
        triggered_rules=triggered,
        triggered_count=len(triggered),
        critical_count=critical_count,
        high_count=high_count,
        primary_issue=primary_issue,
        root_cause=root_cause,
        corrective_actions=all_actions,
        categories_affected=categories,
    )


def evaluate_single_rule(rule_id: str, params: dict) -> TriggeredRule | None:
    """
    Evaluate a single rule by ID against the given parameters.
    Returns a TriggeredRule if the rule fires, None otherwise.
    Useful for targeted testing.
    """
    from app.engine.rules_library import get_rule_by_id
    rule = get_rule_by_id(rule_id)
    if rule is None:
        logger.warning("evaluate_single_rule: rule_id '%s' not found", rule_id)
        return None

    calc = compute_all(params)
    try:
        fired = rule.condition(calc)
    except Exception as exc:
        logger.warning("Rule %s condition raised: %s", rule_id, exc)
        return None

    if not fired:
        return None

    detail = ""
    try:
        detail = rule.engineering_detail(calc)
    except Exception:
        pass

    return TriggeredRule(
        rule_id=rule.rule_id,
        category=rule.category,
        severity=rule.severity,
        fault_name=rule.fault_name,
        root_cause=rule.root_cause,
        corrective_actions=rule.corrective_actions,
        engineering_detail=detail,
    )
