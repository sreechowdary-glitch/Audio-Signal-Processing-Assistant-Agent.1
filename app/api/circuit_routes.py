"""
app/api/circuit_routes.py
-------------------------
POST /api/diagnose

Architecture contract
---------------------
This route is a THIN ORCHESTRATOR only:
  1. Validate circuit parameters
  2. Call app.engine.run_diagnostic()     ← rule engine (38 rules)
  3. Call app.engine.compute_reliability_score() ← reliability assessment
  4. Persist results
  5. Return structured JSON

Zero engineering calculations live in this route.
The Circuit Reliability Score is computed BEFORE Granite is ever called.
"""

import json
import logging
import dataclasses

from flask import Blueprint, current_app, request

from app.extensions import db
from app.models.session        import AnalysisSession
from app.models.circuit_result import CircuitDiagnosticResult
from app.utils.validators     import validate_circuit_params
from app.utils.response_utils import (
    success_response, error_response,
    validation_error_response, server_error_response,
)

logger = logging.getLogger(__name__)
circuit_bp = Blueprint("circuit", __name__)


@circuit_bp.route("/diagnose", methods=["POST"])
def diagnose_circuit():
    """
    POST /api/diagnose
    ------------------
    Receive circuit parameters, run 38-rule engine + reliability assessment.

    JSON Body
    ---------
    {
      "circuit_type":     "non_inverting",
      "op_amp_model":     "LM358",
      "supply_voltage_v": 5.0,
      "gain":             50,
      "input_signal_mv":  500,
      "observed_issue":   "output_clipping",
      "signal_freq_hz":   1000
    }

    Response 200
    ------------
    {
      "success": true,
      "data": {
        "session_uid": "...",
        "calculations": { expected_output_v, closed_loop_bw_hz, ... },
        "triggered_rules": [...],
        "triggered_count": 3,
        "critical_count": 1,
        "primary_issue": "Output Rail Saturation",
        "root_cause": "...",
        "corrective_actions": [...],
        "reliability": {
          "reliability_score": 42,
          "classification": "Poor",
          "power_margin": {...},
          "stability": {...},
          "noise": {...},
          "distortion": {...}
        }
      }
    }
    """
    # ── 1. Parse and validate JSON body ───────────────────────────────────
    data = request.get_json(silent=True)
    if data is None:
        return error_response("Request body must be valid JSON.", 400)

    valid, errors = validate_circuit_params(data)
    if not valid:
        return validation_error_response(errors, "Invalid circuit parameters")

    # ── 2. Create session ─────────────────────────────────────────────────
    session = AnalysisSession(session_type="circuit", status="pending")
    db.session.add(session)
    db.session.flush()

    try:
        # ── 3. Run Rule Engine (all 38 rules — in app.engine) ─────────────
        from app.engine import run_diagnostic
        rule_result = run_diagnostic(data)

        # ── 4. Compute Circuit Reliability Score (in app.engine) ──────────
        from app.engine.health_score import compute_reliability_score
        reliability = compute_reliability_score(rule_result)

        calc = rule_result.calculations

        # ── 5. Serialise triggered rules ──────────────────────────────────
        triggered_list = [
            {
                "rule_id":            r.rule_id,
                "category":           r.category,
                "severity":           r.severity,
                "fault_name":         r.fault_name,
                "root_cause":         r.root_cause,
                "corrective_actions": r.corrective_actions,
                "engineering_detail": r.engineering_detail,
            }
            for r in rule_result.triggered_rules
        ]

        # ── 6. Serialise domain sub-scores ────────────────────────────────
        def _domain_dict(ds) -> dict:
            return {
                "sub_score":          ds.sub_score,
                "risk_level":         ds.risk_level,
                "raw_penalty":        ds.raw_penalty,
                "weighted_penalty":   ds.weighted_penalty,
                "contributing_rules": ds.contributing_rules,
                "penalty_trace":      ds.penalty_trace,
            }

        reliability_dict = {
            "reliability_score":       reliability.reliability_score,
            "classification":          reliability.classification,
            "classification_color":    reliability.classification_color,
            "total_weighted_penalty":  reliability.total_weighted_penalty,
            "primary_risk_domain":     reliability.primary_risk_domain,
            "score_formula":           reliability.score_formula,
            "power_margin":   _domain_dict(reliability.power_margin),
            "stability":      _domain_dict(reliability.stability),
            "noise":          _domain_dict(reliability.noise),
            "distortion":     _domain_dict(reliability.distortion),
        }

        # ── 7. Calculations snapshot ──────────────────────────────────────
        calc_snapshot = {
            "circuit_type":         calc.circuit_type,
            "op_amp_model":         calc.op_amp_model,
            "supply_voltage_v":     calc.supply_voltage_v,
            "gain":                 calc.gain,
            "input_signal_mv":      calc.input_signal_mv,
            "signal_freq_hz":       calc.signal_freq_hz,
            "observed_issue":       calc.observed_issue,
            "expected_output_v":    calc.expected_output_v,
            "output_swing_max_v":   calc.output_swing_max_v,
            "output_headroom_v":    calc.output_headroom_v,
            "headroom_ratio":       calc.headroom_ratio,
            "gbw_hz":               calc.gbw_hz,
            "closed_loop_bw_hz":    calc.closed_loop_bw_hz,
            "noise_gain":           calc.noise_gain,
            "feedback_factor":      calc.feedback_factor,
            "slew_rate_vus":        calc.slew_rate_vus,
            "slew_rate_limit_hz":   calc.slew_rate_limit_hz,
            "slew_rate_headroom_hz":calc.slew_rate_headroom_hz,
            "gbw_margin_hz":        calc.gbw_margin_hz,
            "phase_margin_deg":     calc.phase_margin_deg,
            "output_noise_uv_rms":  calc.output_noise_uv_rms,
            "thermal_noise_uv_rms": calc.thermal_noise_uv_rms,
            "vos_output_error_mv":  calc.vos_output_error_mv,
            "power_dissipation_mw": calc.power_dissipation_mw,
        }

        # ── 8. Persist to database ─────────────────────────────────────────
        circuit_record = CircuitDiagnosticResult(
            session_id=session.id,
            circuit_type=calc.circuit_type,
            op_amp_model=calc.op_amp_model,
            supply_voltage_v=calc.supply_voltage_v,
            gain=calc.gain,
            input_signal_mv=calc.input_signal_mv,
            signal_freq_hz=calc.signal_freq_hz,
            observed_issue=calc.observed_issue,
            expected_output_v=calc.expected_output_v,
            output_swing_headroom_v=calc.output_headroom_v,
            triggered_rules=json.dumps(triggered_list),
            rules_triggered_count=rule_result.triggered_count,
            primary_issue=rule_result.primary_issue,
            root_cause=rule_result.root_cause,
            corrective_actions=json.dumps(rule_result.corrective_actions),
            reliability_score=reliability.reliability_score,
            classification=reliability.classification,
            risk_breakdown=json.dumps({
                "power_margin": _domain_dict(reliability.power_margin),
                "stability":    _domain_dict(reliability.stability),
                "noise":        _domain_dict(reliability.noise),
                "distortion":   _domain_dict(reliability.distortion),
            }),
            score_formula=reliability.score_formula,
            calculations_snapshot=json.dumps(calc_snapshot),
        )
        db.session.add(circuit_record)
        session.status = "complete"
        db.session.commit()

        logger.info(
            "Circuit diagnostic complete: session=%s %s/%s gain=%.0f "
            "score=%d [%s] rules=%d",
            session.session_uid, calc.circuit_type, calc.op_amp_model,
            calc.gain, reliability.reliability_score, reliability.classification,
            rule_result.triggered_count,
        )

        # ── 9. Build response payload ──────────────────────────────────────
        response_data = {
            "session_uid":       session.session_uid,
            "calculations":      calc_snapshot,
            "triggered_rules":   triggered_list,
            "triggered_count":   rule_result.triggered_count,
            "critical_count":    rule_result.critical_count,
            "high_count":        rule_result.high_count,
            "primary_issue":     rule_result.primary_issue,
            "root_cause":        rule_result.root_cause,
            "corrective_actions":rule_result.corrective_actions,
            "categories_affected":rule_result.categories_affected,
            "reliability":       reliability_dict,
        }
        return success_response(response_data, "Circuit diagnostic complete")

    except Exception as exc:
        logger.exception("Circuit diagnostic failed: %s", exc)
        session.status = "failed"
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return server_error_response(f"Diagnostic failed: {str(exc)}")
