"""
app/api/granite_routes.py
-------------------------
POST /api/explain/audio    — Granite explanation for audio analysis
POST /api/explain/circuit  — Granite explanation for circuit diagnostic
POST /api/explain/qa       — Engineering Q&A follow-up
GET  /api/granite/status   — Credential availability check

Architecture contract
---------------------
Granite ONLY receives pre-computed results from the engineering engines.
This route NEVER passes raw parameters or uncomputed values to Granite.
The Circuit Reliability Score MUST already exist in the database before
any explain endpoint is called.

Granite EXPLAINS. It does not CALCULATE.
"""

import json
import logging

from flask import Blueprint, current_app, request

from app.extensions import db
from app.models.session        import AnalysisSession
from app.models.audio_result   import AudioAnalysisResult
from app.models.circuit_result import CircuitDiagnosticResult
from app.utils.validators     import validate_explain_request, validate_qa_request
from app.utils.response_utils import (
    success_response, error_response,
    validation_error_response, server_error_response, not_found_response,
)

logger = logging.getLogger(__name__)
granite_bp = Blueprint("granite", __name__)


# ── Helper: load session by UID ───────────────────────────────────────────────
def _get_session(session_uid: str):
    return AnalysisSession.query.filter_by(
        session_uid=session_uid, status="complete"
    ).first()


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/explain/audio
# ─────────────────────────────────────────────────────────────────────────────
@granite_bp.route("/explain/audio", methods=["POST"])
def explain_audio():
    """
    POST /api/explain/audio
    -----------------------
    Retrieve a completed audio analysis session from the database and
    send its pre-computed results to IBM Granite for explanation.

    JSON Body:  { "session_uid": "..." }

    Granite receives:
      - Signal quality metrics (rms, peak, snr, thd — already computed)
      - Detected faults (already computed with confidence scores)
      - Dominant frequencies (already computed by FFT engine)

    Granite does NOT receive raw audio data or uncomputed parameters.
    """
    data  = request.get_json(silent=True) or {}
    valid, errors = validate_explain_request(data)
    if not valid:
        return validation_error_response(errors)

    session = _get_session(data["session_uid"])
    if not session:
        return not_found_response("Audio session not found or not yet complete.")

    audio = session.audio_result
    if not audio:
        return not_found_response("No audio analysis found for this session.")

    # Build DSP payload from pre-computed DB record
    dsp_payload = {
        "filename":        audio.original_filename,
        "file_format":     audio.file_format,
        "sample_rate":     audio.sample_rate,
        "duration_sec":    audio.duration_sec,
        "rms_dbfs":        audio.rms_dbfs,
        "peak_amplitude":  audio.peak_amplitude,
        "peak_dbfs":       audio.peak_dbfs,
        "crest_factor_db": audio.crest_factor_db,
        "snr_db":          audio.snr_db,
        "dc_offset":       audio.dc_offset,
        "dynamic_range_db":audio.dynamic_range_db,
        "thd_percent":     audio.thd_percent,
        "dominant_freqs":  json.loads(audio.dominant_freqs or "[]"),
        "detected_faults": json.loads(audio.detected_faults or "[]"),
    }

    try:
        from app.ai import explain_audio_analysis
        parsed = explain_audio_analysis(dsp_payload)

        # Cache explanation in DB
        if parsed.available:
            audio.granite_explanation = parsed.summary
            db.session.commit()

        return success_response(
            _serialise_parsed(parsed),
            "Granite explanation generated" if parsed.available else "Granite unavailable",
        )
    except Exception as exc:
        logger.error("Granite audio explain failed: %s", exc)
        return server_error_response(f"Explanation failed: {str(exc)}")


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/explain/circuit
# ─────────────────────────────────────────────────────────────────────────────
@granite_bp.route("/explain/circuit", methods=["POST"])
def explain_circuit():
    """
    POST /api/explain/circuit
    -------------------------
    Retrieve a completed circuit diagnostic from the DB and send its
    pre-computed rule engine + reliability results to Granite.

    JSON Body:  { "session_uid": "..." }

    Granite ONLY receives:
      - Expected output voltage (already computed by circuit_calculator)
      - Triggered rules (already evaluated by rule_engine)
      - Circuit Reliability Score (already computed by health_score engine)

    Granite does NOT recalculate gain, bandwidth, slew rate, or score.
    """
    data  = request.get_json(silent=True) or {}
    valid, errors = validate_explain_request(data)
    if not valid:
        return validation_error_response(errors)

    session = _get_session(data["session_uid"])
    if not session:
        return not_found_response("Circuit session not found or not yet complete.")

    circuit = session.circuit_result
    if not circuit:
        return not_found_response("No circuit diagnostic found for this session.")

    calc_snap = json.loads(circuit.calculations_snapshot or "{}")

    # Build rule payload from pre-computed DB record
    rule_payload = {
        **calc_snap,
        "primary_issue":      circuit.primary_issue,
        "root_cause":         circuit.root_cause,
        "corrective_actions": json.loads(circuit.corrective_actions or "[]"),
        "triggered_rules":    json.loads(circuit.triggered_rules or "[]"),
    }

    risk_bd = json.loads(circuit.risk_breakdown or "{}")
    reliability_payload = {
        "reliability_score":  circuit.reliability_score,
        "classification":     circuit.classification,
        "score_formula":      circuit.score_formula or "",
        **risk_bd,
    }

    try:
        from app.ai import explain_circuit_diagnostic
        parsed = explain_circuit_diagnostic(rule_payload, reliability_payload)

        if parsed.available:
            circuit.granite_explanation = parsed.summary
            db.session.commit()

        return success_response(
            _serialise_parsed(parsed),
            "Granite explanation generated" if parsed.available else "Granite unavailable",
        )
    except Exception as exc:
        logger.error("Granite circuit explain failed: %s", exc)
        return server_error_response(f"Explanation failed: {str(exc)}")


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/explain/qa
# ─────────────────────────────────────────────────────────────────────────────
@granite_bp.route("/explain/qa", methods=["POST"])
def engineering_qa():
    """
    POST /api/explain/qa
    --------------------
    Answer a follow-up engineering question anchored to a completed session.

    JSON Body:
    {
      "session_uid":  "...",
      "question":     "Why is the output clipping?",
      "session_type": "circuit"   // or "audio"
    }
    """
    data = request.get_json(silent=True) or {}
    valid, errors = validate_qa_request(data)
    if not valid:
        return validation_error_response(errors)

    session = _get_session(data["session_uid"])
    if not session:
        return not_found_response("Session not found or not yet complete.")

    # Build condensed context from whichever result type exists
    context: dict = {}
    if session.session_type == "audio" and session.audio_result:
        ar = session.audio_result
        context = {
            "filename":     ar.original_filename,
            "sample_rate":  ar.sample_rate,
            "snr_db":       ar.snr_db,
            "rms_dbfs":     ar.rms_dbfs,
            "peak_dbfs":    ar.peak_dbfs,
            "faults":       json.loads(ar.detected_faults or "[]"),
        }
    elif session.session_type == "circuit" and session.circuit_result:
        cr = session.circuit_result
        snap = json.loads(cr.calculations_snapshot or "{}")
        context = {
            "circuit_type":       cr.circuit_type,
            "op_amp_model":       cr.op_amp_model,
            "supply_voltage_v":   cr.supply_voltage_v,
            "gain":               cr.gain,
            "expected_output_v":  snap.get("expected_output_v"),
            "reliability_score":  cr.reliability_score,
            "classification":     cr.classification,
            "primary_issue":      cr.primary_issue,
        }

    try:
        from app.ai import answer_engineering_question
        parsed = answer_engineering_question(
            question=data["question"],
            context=context,
            session_type=data.get("session_type", session.session_type),
        )
        return success_response(_serialise_parsed(parsed))
    except Exception as exc:
        logger.error("Granite QA failed: %s", exc)
        return server_error_response(f"Q&A failed: {str(exc)}")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/granite/status
# ─────────────────────────────────────────────────────────────────────────────
@granite_bp.route("/granite/status", methods=["GET"])
def granite_status():
    """
    GET /api/granite/status
    -----------------------
    Return IBM Granite credential availability without making an API call.
    Used by the frontend to show the status dot in the topbar.
    """
    try:
        from app.ai import get_granite_status
        status = get_granite_status()
        return success_response(status)
    except Exception as exc:
        return success_response({"available": False, "error": str(exc)})


# ── Helper: serialise ParsedGraniteResponse to dict ──────────────────────────
def _serialise_parsed(parsed) -> dict:
    return {
        "available":           parsed.available,
        "summary":             parsed.summary,
        "root_cause_analysis": parsed.root_cause_analysis,
        "recommendations":     parsed.recommendations,
        "technical_notes":     parsed.technical_notes,
        "parse_warnings":      parsed.parse_warnings,
        "model_id":            parsed.model_id,
        "input_tokens":        parsed.input_tokens,
        "output_tokens":       parsed.output_tokens,
        "latency_ms":          parsed.latency_ms,
    }
