"""
prompt_builder.py
-----------------
Constructs structured, constrained prompts for IBM Granite.

Architecture constraint (enforced in every prompt template)
-----------------------------------------------------------
Every prompt:
  1. Opens with an explicit ROLE instruction that forbids Granite from
     performing calculations.
  2. Injects ALL pre-computed engineering values as labelled FACTS.
  3. Instructs Granite to explain, interpret, and recommend — never derive.
  4. Closes with a strict OUTPUT FORMAT block so response_parser.py can
     extract structured sections reliably.

Three prompt types
------------------
    build_audio_prompt()      — for DSP analysis results
    build_circuit_prompt()    — for Rule Engine + CircuitReliabilityResult
    build_qa_prompt()         — for follow-up engineering Q&A

All inputs are plain Python dicts or dataclasses — no raw audio data,
no NumPy arrays, no unprocessed signal values ever enter a prompt.

Forbidden content (never injected into prompts)
-----------------------------------------------
  - Raw audio sample arrays
  - NumPy / binary data
  - Unevaluated rule conditions
  - Uncomputed formula variables

Dependencies: dataclasses from DSP and engine packages (type-hinted only;
imported inside functions to allow standalone testing).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Shared prompt header — injected into every prompt type
# ─────────────────────────────────────────────────────────────────────────────
_ROLE_HEADER = """\
You are a Senior Audio Electronics Engineering Assistant integrated into the
Audio Intelligence & Circuit Diagnostic Platform.

STRICT RULES — you must follow these at all times:
1. You are an EXPLANATION engine only. Never perform or re-derive engineering calculations.
2. All numeric values in the FACTS section below were computed by certified DSP
   and rule-based engineering engines. Treat them as ground truth.
3. Do not recalculate gain, bandwidth, slew rate, SNR, THD, or any other metric.
4. Do not evaluate or re-run engineering rules. The rule engine has already done this.
5. Do not generate or modify the Circuit Reliability Score. It is already computed.
6. Your role: explain WHY findings matter, WHAT they mean for the engineer,
   and HOW the recommended actions will fix the problem — in plain engineering language.
7. Be concise. Use engineering terminology appropriate for an ECE student or junior engineer.
8. If a value looks unusual, explain it — do not correct it.
"""

_OUTPUT_FORMAT = """\

---OUTPUT FORMAT---
Respond with exactly these four sections, each preceded by its header line.
Do not add extra sections. Do not use markdown headers.

SUMMARY:
[2-3 sentences summarising the overall condition of the circuit/signal.]

ROOT CAUSE ANALYSIS:
[3-5 sentences explaining the engineering root cause of the primary issue.]

RECOMMENDATIONS:
[Numbered list of 3-5 specific, actionable engineering recommendations.]

TECHNICAL NOTES:
[1-3 sentences of additional context, caveats, or follow-up observations.]
---END FORMAT---
"""


# ─────────────────────────────────────────────────────────────────────────────
# Prompt 1 — Audio DSP Analysis
# ─────────────────────────────────────────────────────────────────────────────
def build_audio_prompt(dsp_payload: dict[str, Any]) -> str:
    """
    Build a Granite explanation prompt from a DSP analysis result dict.

    Parameters
    ----------
    dsp_payload : dict produced by the Flask route from FullAnalysisResult.
        Expected keys (all pre-computed by the DSP engine):
            filename, file_format, sample_rate, duration_sec,
            rms_dbfs, peak_amplitude, peak_dbfs, crest_factor_db, snr_db,
            dc_offset, dynamic_range_db, dominant_freqs (list of dicts),
            detected_faults (list of dicts with fault/confidence/severity/explanation),
            thd_percent (optional)

    Returns
    -------
    str — complete prompt ready to send to granite_client.generate()
    """
    # ── Extract fields with safe defaults ───────────────────────────────────
    filename       = dsp_payload.get("filename", "unknown")
    file_format    = dsp_payload.get("file_format", "unknown").upper()
    sample_rate    = dsp_payload.get("sample_rate", 0)
    duration_sec   = dsp_payload.get("duration_sec", 0.0)
    rms_dbfs       = dsp_payload.get("rms_dbfs", 0.0)
    peak_amplitude = dsp_payload.get("peak_amplitude", 0.0)
    peak_dbfs      = dsp_payload.get("peak_dbfs", 0.0)
    crest_factor   = dsp_payload.get("crest_factor_db", 0.0)
    snr_db         = dsp_payload.get("snr_db", 0.0)
    dc_offset      = dsp_payload.get("dc_offset", 0.0)
    dyn_range      = dsp_payload.get("dynamic_range_db", 0.0)
    thd_percent    = dsp_payload.get("thd_percent")

    dominant_freqs  = dsp_payload.get("dominant_freqs", [])
    detected_faults = dsp_payload.get("detected_faults", [])

    # ── Format dominant frequencies ─────────────────────────────────────────
    freq_lines = "\n".join(
        f"  {i+1}. {f.get('freq_hz', 0):.1f} Hz  |  {f.get('magnitude_db', 0):.1f} dBFS"
        for i, f in enumerate(dominant_freqs[:5])
    ) or "  (none detected)"

    # ── Format detected faults ───────────────────────────────────────────────
    fault_lines = []
    for fault in detected_faults:
        if fault.get("detected", False):
            fault_lines.append(
                f"  FAULT: {fault.get('fault', '?')}\n"
                f"    Confidence: {fault.get('confidence', 0):.1%}\n"
                f"    Severity:   {fault.get('severity', '?')}\n"
                f"    Engineering finding: {fault.get('explanation', '')}"
            )
    faults_block = "\n".join(fault_lines) if fault_lines else "  No faults detected."

    thd_line = (
        f"  THD:                {thd_percent:.3f}%"
        if thd_percent is not None
        else "  THD:                not computed (insufficient harmonic content)"
    )

    prompt = f"""{_ROLE_HEADER}
---AUDIO SIGNAL ANALYSIS FACTS---
These values were computed by the DSP Analysis Engine. Do not recalculate them.

File:             {filename} ({file_format})
Sample Rate:      {sample_rate:,} Hz
Duration:         {duration_sec:.3f} s

SIGNAL QUALITY METRICS (computed by DSP engine):
  RMS Level:          {rms_dbfs:.2f} dBFS
  Peak Amplitude:     {peak_amplitude:.5f} ({peak_dbfs:.2f} dBFS)
  Crest Factor:       {crest_factor:.2f} dB
  SNR (estimated):    {snr_db:.2f} dB
  DC Offset:          {dc_offset:.6f} (ideal = 0.0)
  Dynamic Range:      {dyn_range:.2f} dB
{thd_line}

DOMINANT FREQUENCIES (computed by FFT engine):
{freq_lines}

DETECTED FAULTS (computed by fault detection engine):
{faults_block}
---END FACTS---

TASK:
Using only the FACTS above, provide an engineering explanation of this audio signal's
condition. Explain what the metrics mean for audio quality, why the detected faults
matter, and what an engineer should do to address them.
Do NOT recalculate any of the values above.
{_OUTPUT_FORMAT}"""

    logger.info(
        "Audio prompt built: filename=%s, %d faults detected, prompt_len=%d chars",
        filename, len(fault_lines), len(prompt),
    )
    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# Prompt 2 — Circuit Diagnostic + Reliability Score
# ─────────────────────────────────────────────────────────────────────────────
def build_circuit_prompt(
    rule_payload:        dict[str, Any],
    reliability_payload: dict[str, Any],
) -> str:
    """
    Build a Granite explanation prompt from Rule Engine + CircuitReliabilityResult.

    Parameters
    ----------
    rule_payload : dict from RuleEvaluationResult serialisation.
        Expected keys:
            circuit_type, op_amp_model, supply_voltage_v, gain,
            input_signal_mv, signal_freq_hz, observed_issue,
            expected_output_v, output_headroom_v, headroom_ratio,
            closed_loop_bw_hz, slew_rate_limit_hz, phase_margin_deg,
            output_noise_uv_rms, vos_output_error_mv,
            triggered_rules (list of dicts), primary_issue, root_cause,
            corrective_actions (list of str)

    reliability_payload : dict from CircuitReliabilityResult serialisation.
        Expected keys:
            reliability_score, classification,
            power_margin (dict), stability (dict), noise (dict),
            distortion (dict), primary_risk_domain, score_formula

    Returns
    -------
    str — complete prompt ready for Granite
    """
    # ── Rule engine fields ───────────────────────────────────────────────────
    ct            = rule_payload.get("circuit_type", "unknown")
    model         = rule_payload.get("op_amp_model", "unknown")
    supply        = rule_payload.get("supply_voltage_v", 0.0)
    gain          = rule_payload.get("gain", 0.0)
    vin_mv        = rule_payload.get("input_signal_mv", 0.0)
    freq_hz       = rule_payload.get("signal_freq_hz", 1000.0)
    issue         = rule_payload.get("observed_issue", "none")
    vout_exp      = rule_payload.get("expected_output_v", 0.0)
    headroom      = rule_payload.get("output_headroom_v", 0.0)
    headroom_pct  = rule_payload.get("headroom_ratio", 0.0) * 100
    cl_bw         = rule_payload.get("closed_loop_bw_hz", 0.0)
    slew_lim      = rule_payload.get("slew_rate_limit_hz", 0.0)
    phase_m       = rule_payload.get("phase_margin_deg", 0.0)
    out_noise     = rule_payload.get("output_noise_uv_rms", 0.0)
    vos_err       = rule_payload.get("vos_output_error_mv", 0.0)
    primary_issue = rule_payload.get("primary_issue", "None")
    root_cause    = rule_payload.get("root_cause", "")
    actions       = rule_payload.get("corrective_actions", [])
    triggered     = rule_payload.get("triggered_rules", [])

    # ── Reliability score fields ─────────────────────────────────────────────
    score        = reliability_payload.get("reliability_score", 0)
    classification = reliability_payload.get("classification", "Unknown")
    pm_dom       = reliability_payload.get("power_margin",  {})
    st_dom       = reliability_payload.get("stability",     {})
    no_dom       = reliability_payload.get("noise",         {})
    di_dom       = reliability_payload.get("distortion",    {})
    formula      = reliability_payload.get("score_formula", "")

    # ── Format triggered rules block ─────────────────────────────────────────
    rule_lines = []
    for r in triggered:
        rule_lines.append(
            f"  [{r.get('rule_id','?')}] {r.get('severity','?').upper()}: "
            f"{r.get('fault_name','?')}\n"
            f"    Engineering detail: {r.get('engineering_detail','')}"
        )
    rules_block = "\n".join(rule_lines) if rule_lines else "  No rules triggered."

    # ── Format corrective actions ────────────────────────────────────────────
    actions_block = "\n".join(
        f"  {i+1}. {a}" for i, a in enumerate(actions[:8])
    ) or "  No actions specified."

    # ── Format domain scores ─────────────────────────────────────────────────
    def _dom(d: dict) -> str:
        return (
            f"sub_score={d.get('sub_score', 0):.1f}/100  "
            f"risk={d.get('risk_level', '?')}  "
            f"weighted_penalty={d.get('weighted_penalty', 0):.2f}"
        )

    prompt = f"""{_ROLE_HEADER}
---CIRCUIT DIAGNOSTIC FACTS---
These values were computed by the Rule-Based Circuit Diagnostic Engine and
the Circuit Reliability Assessment Engine. Do not recalculate any of them.

CIRCUIT CONFIGURATION:
  Circuit Type:       {ct.replace('_', ' ').title()}
  Op-Amp Model:       {model}
  Supply Voltage:     ±{supply:.1f} V
  Configured Gain:    {gain:.1f}x
  Input Signal:       {vin_mv:.1f} mV
  Signal Frequency:   {freq_hz:.0f} Hz
  Reported Issue:     {issue.replace('_', ' ').title()}

COMPUTED ENGINEERING VALUES (calculated by circuit_calculator.py):
  Expected Output:        {vout_exp:.4f} V
  Output Headroom:        {headroom:.4f} V  ({headroom_pct:.1f}% of supply)
  Closed-Loop Bandwidth:  {cl_bw:.0f} Hz
  Slew Rate Limit:        {slew_lim:.0f} Hz
  Phase Margin (est.):    {phase_m:.1f} degrees
  Output Noise:           {out_noise:.2f} µVrms
  Vos Output Error:       {vos_err:.2f} mV

PRIMARY ISSUE (identified by rule engine):
  {primary_issue}

ROOT CAUSE (determined by rule engine):
  {root_cause}

TRIGGERED ENGINEERING RULES ({len(triggered)} rules fired):
{rules_block}

CORRECTIVE ACTIONS (from rule engine):
{actions_block}

CIRCUIT RELIABILITY SCORE (computed by Circuit Reliability Assessment Engine):
  Score:          {score}/100
  Classification: {classification}
  Power Margin:   {_dom(pm_dom)}
  Stability:      {_dom(st_dom)}
  Noise:          {_dom(no_dom)}
  Distortion:     {_dom(di_dom)}
  Derivation:     {formula.splitlines()[0] if formula else ''}
---END FACTS---

TASK:
Using only the FACTS above, provide an engineering explanation of this circuit's
condition. Explain why the triggered rules indicate a problem, what the Circuit
Reliability Score reflects about the circuit's condition, and why the recommended
corrective actions will resolve the issues.
Do NOT recalculate the expected output, gain, bandwidth, slew rate, or reliability score.
Do NOT re-evaluate rules. Explain the pre-computed results.
{_OUTPUT_FORMAT}"""

    logger.info(
        "Circuit prompt built: %s/%s, score=%d [%s], %d rules, prompt_len=%d chars",
        ct, model, score, classification, len(triggered), len(prompt),
    )
    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# Prompt 3 — Engineering Q&A
# ─────────────────────────────────────────────────────────────────────────────
def build_qa_prompt(
    question:    str,
    context:     dict[str, Any],
    session_type: str = "circuit",
) -> str:
    """
    Build a follow-up Q&A prompt anchored to a prior analysis session.

    Parameters
    ----------
    question     : the user's natural-language engineering question
    context      : condensed session facts dict (subset of rule or DSP payload)
    session_type : 'circuit' | 'audio'

    Returns
    -------
    str — prompt for Granite
    """
    if not question.strip():
        question = "Can you summarise the main findings from this analysis?"

    # Build a brief context block from whatever keys are available
    ctx_lines = []
    for k, v in context.items():
        if isinstance(v, (str, int, float, bool)) and v not in ("", None):
            ctx_lines.append(f"  {k.replace('_', ' ').title()}: {v}")
    context_block = "\n".join(ctx_lines[:20]) or "  No additional context available."

    prompt = f"""{_ROLE_HEADER}
---SESSION CONTEXT ({session_type.upper()} ANALYSIS)---
{context_block}
---END CONTEXT---

ENGINEER'S QUESTION:
{question.strip()}

TASK:
Answer the engineer's question using only the context facts provided above.
Do not perform any calculations. Do not evaluate rules.
If the question asks you to compute a value, politely redirect:
state that calculations are performed by the engineering engine, and
explain the relevant engineering principle instead.
{_OUTPUT_FORMAT}"""

    logger.info(
        "QA prompt built: session_type=%s, question_len=%d chars",
        session_type, len(question),
    )
    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helpers — convert dataclasses to dicts for prompt builders
# ─────────────────────────────────────────────────────────────────────────────
def serialise_rule_result(rule_result: Any) -> dict:
    """
    Flatten a RuleEvaluationResult into a plain dict safe for prompt injection.
    Accepts the dataclass directly or a pre-serialised dict.
    """
    if isinstance(rule_result, dict):
        return rule_result

    calc = rule_result.calculations
    return {
        "circuit_type":         calc.circuit_type,
        "op_amp_model":         calc.op_amp_model,
        "supply_voltage_v":     calc.supply_voltage_v,
        "gain":                 calc.gain,
        "input_signal_mv":      calc.input_signal_mv,
        "signal_freq_hz":       calc.signal_freq_hz,
        "observed_issue":       calc.observed_issue,
        "expected_output_v":    calc.expected_output_v,
        "output_headroom_v":    calc.output_headroom_v,
        "headroom_ratio":       calc.headroom_ratio,
        "closed_loop_bw_hz":    calc.closed_loop_bw_hz,
        "slew_rate_limit_hz":   calc.slew_rate_limit_hz,
        "phase_margin_deg":     calc.phase_margin_deg,
        "output_noise_uv_rms":  calc.output_noise_uv_rms,
        "vos_output_error_mv":  calc.vos_output_error_mv,
        "primary_issue":        rule_result.primary_issue,
        "root_cause":           rule_result.root_cause,
        "corrective_actions":   rule_result.corrective_actions,
        "triggered_rules": [
            {
                "rule_id":           r.rule_id,
                "severity":          r.severity,
                "fault_name":        r.fault_name,
                "engineering_detail": r.engineering_detail,
            }
            for r in rule_result.triggered_rules
        ],
    }


def serialise_reliability_result(reliability_result: Any) -> dict:
    """
    Flatten a CircuitReliabilityResult into a plain dict for prompt injection.
    Accepts the dataclass directly or a pre-serialised dict.
    """
    if isinstance(reliability_result, dict):
        return reliability_result

    def _dom(ds: Any) -> dict:
        return {
            "sub_score":         ds.sub_score,
            "risk_level":        ds.risk_level,
            "weighted_penalty":  ds.weighted_penalty,
            "contributing_rules": ds.contributing_rules,
        }

    return {
        "reliability_score":      reliability_result.reliability_score,
        "classification":         reliability_result.classification,
        "total_weighted_penalty": reliability_result.total_weighted_penalty,
        "primary_risk_domain":    reliability_result.primary_risk_domain,
        "score_formula":          reliability_result.score_formula,
        "power_margin":           _dom(reliability_result.power_margin),
        "stability":              _dom(reliability_result.stability),
        "noise":                  _dom(reliability_result.noise),
        "distortion":             _dom(reliability_result.distortion),
    }


def serialise_dsp_result(full_result: Any) -> dict:
    """
    Flatten a FullAnalysisResult into a plain dict for the audio prompt.
    Accepts the dataclass directly or a pre-serialised dict.
    """
    if isinstance(full_result, dict):
        return full_result

    audio   = full_result.audio
    metrics = full_result.metrics
    fft     = full_result.fft
    faults  = full_result.faults

    return {
        "filename":        audio.filename,
        "file_format":     audio.file_format,
        "sample_rate":     audio.sample_rate,
        "duration_sec":    audio.duration_sec,
        "rms_dbfs":        metrics.rms_dbfs,
        "peak_amplitude":  metrics.peak_amplitude,
        "peak_dbfs":       metrics.peak_dbfs,
        "crest_factor_db": metrics.crest_factor_db,
        "snr_db":          metrics.snr_db,
        "dc_offset":       metrics.dc_offset,
        "dynamic_range_db": metrics.dynamic_range_db,
        "thd_percent": next(
            (f.detail.get("thd_percent") for f in faults if f.fault == "Distortion (THD)"),
            None,
        ),
        "dominant_freqs": [
            {"freq_hz": p.freq_hz, "magnitude_db": p.magnitude_db}
            for p in fft.dominant_freqs[:5]
        ],
        "detected_faults": [
            {
                "fault":       f.fault,
                "detected":    f.detected,
                "confidence":  f.confidence,
                "severity":    f.severity,
                "explanation": f.explanation,
            }
            for f in faults
        ],
    }
