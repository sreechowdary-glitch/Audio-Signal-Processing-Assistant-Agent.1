"""
app/ai/__init__.py
------------------
Public interface for the IBM Granite Explanation Layer.

Architecture position
---------------------
This package sits ABOVE the engineering engines and ONLY calls them to
receive pre-computed results. It never imports from app.dsp or app.engine
to perform calculations — it imports their result dataclasses solely to
read pre-computed values for prompt construction.

Call chain (enforced by design):
    app.dsp         → FullAnalysisResult          ─┐
    app.engine      → RuleEvaluationResult          ├─► prompt_builder ─► granite_client ─► response_parser
    app.engine      → CircuitReliabilityResult     ─┘

Public API
----------
    explain_audio_analysis(dsp_result)
        Takes a FullAnalysisResult (or its serialised dict).
        Returns ParsedGraniteResponse.

    explain_circuit_diagnostic(rule_result, reliability_result)
        Takes a RuleEvaluationResult + CircuitReliabilityResult (or dicts).
        Returns ParsedGraniteResponse.

    answer_engineering_question(question, context, session_type)
        Takes a natural-language question + condensed context dict.
        Returns ParsedGraniteResponse.

    check_granite_available()
        Returns bool — True if credentials are configured.

Granite Responsibilities (this layer enforces):
    YES → explain DSP findings
    YES → explain circuit rule violations
    YES → interpret Circuit Reliability Score meaning
    YES → suggest why recommendations fix the problem
    YES → answer engineering Q&A in plain language

    NO  → calculate gain, bandwidth, slew rate, SNR, THD
    NO  → evaluate engineering rules
    NO  → generate or modify Circuit Reliability Score
    NO  → make decisions about circuit correctness
"""

import logging
from typing import Any

from app.ai.granite_client  import GraniteResponse, generate, check_credentials
from app.ai.prompt_builder  import (
    build_audio_prompt,
    build_circuit_prompt,
    build_qa_prompt,
    serialise_dsp_result,
    serialise_rule_result,
    serialise_reliability_result,
)
from app.ai.response_parser import ParsedGraniteResponse, parse_response, parse_raw_text

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public API — three explanation entry points
# ─────────────────────────────────────────────────────────────────────────────

def explain_audio_analysis(dsp_result: Any) -> ParsedGraniteResponse:
    """
    Generate a Granite explanation for a completed audio DSP analysis.

    Parameters
    ----------
    dsp_result : FullAnalysisResult dataclass OR pre-serialised dict.
        All numeric values must already be computed by app.dsp engines.

    Returns
    -------
    ParsedGraniteResponse — always valid.
        If Granite is unavailable, available=False with engineering fallback text.
    """
    logger.info("=== Granite: explain_audio_analysis() ===")

    # Serialise dataclass to dict if needed
    dsp_payload = serialise_dsp_result(dsp_result)

    # Build constrained prompt — all engineering values injected as FACTS
    prompt = build_audio_prompt(dsp_payload)

    # Transport to Granite
    granite_resp = generate(prompt)

    # Parse and structure the response
    parsed = parse_response(granite_resp)

    logger.info(
        "=== Audio explanation: available=%s, warnings=%d ===",
        parsed.available, len(parsed.parse_warnings),
    )
    return parsed


def explain_circuit_diagnostic(
    rule_result:        Any,
    reliability_result: Any,
) -> ParsedGraniteResponse:
    """
    Generate a Granite explanation for a completed circuit diagnostic.

    Parameters
    ----------
    rule_result         : RuleEvaluationResult dataclass OR pre-serialised dict.
                          All rule evaluations must already be complete.
    reliability_result  : CircuitReliabilityResult dataclass OR pre-serialised dict.
                          Circuit Reliability Score must already be computed.

    Returns
    -------
    ParsedGraniteResponse — always valid.

    Architecture guarantee
    ----------------------
    This function NEVER calls app.engine. It only reads pre-computed results.
    The Circuit Reliability Score is ALREADY computed before this function runs.
    """
    logger.info("=== Granite: explain_circuit_diagnostic() ===")

    rule_payload        = serialise_rule_result(rule_result)
    reliability_payload = serialise_reliability_result(reliability_result)

    prompt = build_circuit_prompt(rule_payload, reliability_payload)

    granite_resp = generate(prompt)
    parsed       = parse_response(granite_resp)

    logger.info(
        "=== Circuit explanation: available=%s, score=%d, warnings=%d ===",
        parsed.available,
        reliability_payload.get("reliability_score", 0),
        len(parsed.parse_warnings),
    )
    return parsed


def answer_engineering_question(
    question:    str,
    context:     dict[str, Any],
    session_type: str = "circuit",
) -> ParsedGraniteResponse:
    """
    Answer a follow-up engineering question anchored to a prior analysis session.

    Parameters
    ----------
    question     : natural-language engineering question from the user
    context      : condensed dict of session facts (subset of rule or DSP payload)
    session_type : 'circuit' | 'audio'

    Returns
    -------
    ParsedGraniteResponse
    """
    logger.info(
        "=== Granite: answer_engineering_question(), session_type=%s ===",
        session_type,
    )

    prompt       = build_qa_prompt(question, context, session_type)
    granite_resp = generate(prompt)
    parsed       = parse_response(granite_resp)

    logger.info("=== QA explanation: available=%s ===", parsed.available)
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def check_granite_available() -> bool:
    """
    Return True if IBM Granite credentials are configured in environment variables.
    Does not make an API call.
    """
    creds = check_credentials()
    return creds["all_configured"]


def get_granite_status() -> dict:
    """
    Return a detailed credential status dict for the /api/health endpoint.
    Safe to expose to the frontend — no secret values included.
    """
    creds = check_credentials()
    return {
        "available":        creds["all_configured"],
        "model_id":         creds["model_id"],
        "api_key_set":      creds["api_key_set"],
        "project_id_set":   creds["project_id_set"],
        "base_url_set":     creds["base_url_set"],
        "note": (
            "IBM Granite is configured and ready."
            if creds["all_configured"]
            else "IBM Granite credentials missing. Engineering diagnostics remain fully functional."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Module exports
# ─────────────────────────────────────────────────────────────────────────────
__all__ = [
    # Core explanation functions
    "explain_audio_analysis",
    "explain_circuit_diagnostic",
    "answer_engineering_question",
    # Utilities
    "check_granite_available",
    "get_granite_status",
    # Sub-module re-exports (for direct import by routes/tests)
    "GraniteResponse",
    "ParsedGraniteResponse",
    "generate",
    "build_audio_prompt",
    "build_circuit_prompt",
    "build_qa_prompt",
    "parse_response",
    "parse_raw_text",
    "serialise_dsp_result",
    "serialise_rule_result",
    "serialise_reliability_result",
]
