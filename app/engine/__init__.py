"""
app/engine/__init__.py
----------------------
Public interface for the Rule-Based Circuit Diagnostic Engine
and the Circuit Reliability Assessment Engine.

Exports the top-level orchestrators run_diagnostic() and
assess_circuit_reliability() that Flask routes and the PDF generator call,
plus all key dataclasses needed by downstream consumers.

Terminology
-----------
    run_diagnostic()            → evaluates 38 rules, returns RuleEvaluationResult
    assess_circuit_reliability()→ computes Circuit Reliability Score, returns CircuitReliabilityResult
    CircuitReliabilityResult    → replaces the old "HealthScoreResult" name
    reliability_score           → the 0–100 integer shown as "Circuit Reliability Score"
"""

from app.engine.circuit_calculator import (
    CircuitCalculations,
    compute_all,
    get_op_amp_specs,
    OP_AMP_SPECS,
)
from app.engine.rules_library import (
    RuleDefinition,
    RULES,
    get_rule_by_id,
    get_rules_by_category,
)
from app.engine.rule_engine import (
    TriggeredRule,
    RuleEvaluationResult,
    evaluate,
    evaluate_single_rule,
)
from app.engine.health_score import (
    DomainScore,
    CircuitReliabilityResult,
    HealthScoreResult,          # backward-compatible alias
    compute_reliability_score,
)

import logging
logger = logging.getLogger(__name__)


def run_diagnostic(params: dict) -> RuleEvaluationResult:
    """
    Run all 38 engineering rules against circuit parameters.

    Parameters
    ----------
    params : dict with keys:
        circuit_type     : str  — 'non_inverting' | 'inverting' | 'difference' |
                                   'integrator'   | 'comparator'
        op_amp_model     : str  — 'LM358' | 'TL071' | 'NE5532' | 'LM741' |
                                   'LM386' | 'OPA2134' | 'AD8221' | 'GENERIC'
        supply_voltage_v : float — positive magnitude (e.g. 5.0 for ±5V supply)
        gain             : float — configured amplifier gain
        input_signal_mv  : float — input signal amplitude in millivolts
        observed_issue   : str  — 'output_clipping' | 'oscillation' | 'hum_noise' |
                                   'no_output' | 'distortion' | 'gain_instability' |
                                   'low_output' | 'ground_loop' | 'none'
        signal_freq_hz   : float — signal frequency in Hz (default: 1000.0)

    Returns
    -------
    RuleEvaluationResult
    """
    logger.info("=== Circuit Diagnostic Engine: run_diagnostic() ===")
    result = evaluate(params)
    logger.info(
        "=== Diagnostic complete: %d rules triggered, primary_issue='%s' ===",
        result.triggered_count,
        result.primary_issue,
    )
    return result


def assess_circuit_reliability(params: dict) -> CircuitReliabilityResult:
    """
    Full pipeline: run_diagnostic() → compute_reliability_score().

    Convenience orchestrator that returns a CircuitReliabilityResult
    directly from raw circuit parameter dict — no intermediate object needed.

    The returned CircuitReliabilityResult carries:
        .reliability_score  → "Circuit Reliability Score" (0–100 integer)
        .classification     → 'Excellent' | 'Good' | 'Fair' | 'Poor' | 'Critical'
        .power_margin       → DomainScore  (label: "Power Margin")
        .stability          → DomainScore  (label: "Stability")
        .noise              → DomainScore  (label: "Noise")
        .distortion         → DomainScore  (label: "Distortion")
        .score_formula      → human-readable derivation string

    Dashboard label : "Circuit Reliability Score"
    PDF section     : "Circuit Reliability Assessment"
    PDF score line  : "Circuit Reliability Score: {n}/100"
    """
    logger.info("=== Circuit Reliability Assessment Engine: assess_circuit_reliability() ===")
    rule_result       = evaluate(params)
    reliability_result = compute_reliability_score(rule_result)
    logger.info(
        "=== Assessment complete: Circuit Reliability Score=%d [%s] ===",
        reliability_result.reliability_score,
        reliability_result.classification,
    )
    return reliability_result


__all__ = [
    # Calculator
    "CircuitCalculations", "compute_all", "get_op_amp_specs", "OP_AMP_SPECS",
    # Rules
    "RuleDefinition", "RULES", "get_rule_by_id", "get_rules_by_category",
    # Rule engine results
    "TriggeredRule", "RuleEvaluationResult",
    # Circuit Reliability Assessment Engine — primary names
    "DomainScore", "CircuitReliabilityResult", "compute_reliability_score",
    # Backward-compatible alias (deprecated — use CircuitReliabilityResult)
    "HealthScoreResult",
    # Orchestrators
    "run_diagnostic", "evaluate", "evaluate_single_rule",
    "assess_circuit_reliability",
]
