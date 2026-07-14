"""
health_score.py
---------------
Deterministic, fully traceable Circuit Reliability Assessment Engine.

Formula
-------
    Circuit Reliability Score (0–100) = 100 − Weighted Penalty Total

    Penalty contributors (four sub-domains, weights sum to 100):

        Power Margin penalty     × 0.30  (30 points max)
        Stability penalty        × 0.25  (25 points max)
        Noise penalty            × 0.25  (25 points max)
        Distortion penalty       × 0.20  (20 points max)

Each sub-domain penalty is computed from:
  1. Rule engine output  — triggered rules in that category contribute
     a rule-specific penalty value (defined in _RULE_PENALTIES below).
  2. Engineering calculations — continuous penalty functions derived
     directly from CircuitCalculations numeric quantities (headroom ratio,
     phase margin, output noise, etc.).

The final score is clamped to [0, 100].  Every point of deduction is
traceable to an explicit engineering condition.

Risk classification
-------------------
    90–100  Excellent   — all parameters within comfortable margins
    75–89   Good        — minor issues present, monitor
    55–74   Fair        — notable issues, corrective action recommended
    35–54   Poor        — significant violations, immediate review required
    0–34    Critical    — severe violations, circuit likely non-functional

No AI calls. No estimation. No randomness.

Terminology
-----------
    "Circuit Reliability Score"      → the final 0–100 integer displayed on the dashboard
    "Circuit Reliability Assessment" → the full engine and its result object
    "Health Score" / "HealthScore"   → deprecated names; do not use in new code

Dependencies: app.engine.circuit_calculator, app.engine.rule_engine (internal)
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.engine.circuit_calculator import CircuitCalculations
from app.engine.rule_engine import RuleEvaluationResult, TriggeredRule

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Weight distribution (must sum to 1.0)
# ─────────────────────────────────────────────────────────────────────────────
WEIGHT_POWER_MARGIN  = 0.30   # 30 points — saturation kills functionality
WEIGHT_STABILITY     = 0.25   # 25 points — oscillation makes circuit unusable
WEIGHT_NOISE         = 0.25   # 25 points — SNR is core audio quality metric
WEIGHT_DISTORTION    = 0.20   # 20 points — THD/linearity

assert abs(WEIGHT_POWER_MARGIN + WEIGHT_STABILITY + WEIGHT_NOISE + WEIGHT_DISTORTION - 1.0) < 1e-9

# Maximum raw penalty per domain (before weighting) = 100
_MAX_DOMAIN = 100.0

# ─────────────────────────────────────────────────────────────────────────────
# Rule-to-domain penalty map
# Each entry: rule_id → (domain, raw_penalty 0–100)
# Domain keys: 'power', 'stability', 'noise', 'distortion'
# ─────────────────────────────────────────────────────────────────────────────
_RULE_PENALTIES: dict[str, tuple[str, float]] = {
    # Output Saturation / Supply Rail → POWER domain
    "R01": ("power",      100),   # full saturation = maximum penalty
    "R02": ("power",       85),   # swing limit exceeded
    "R03": ("power",       55),   # critically low headroom
    "R04": ("power",      100),   # below absolute minimum supply
    "R05": ("power",       90),   # LM741 below minimum supply
    "R06": ("power",       30),   # approaching Vmax (warning)
    "R07": ("power",       45),   # compressed dynamic range

    # Gain misconfiguration → splits across distortion and stability
    "R08": ("distortion",  60),   # gain > 100 → THD risk
    "R09": ("noise",       50),   # BW below audio → HF rolloff
    "R10": ("stability",   40),   # impossible gain < 1
    "R11": ("noise",       45),   # DC offset error
    "R12": ("stability",    5),   # info: unity gain — minimal penalty
    "R13": ("noise",       30),   # noise gain penalty

    # Bandwidth / slew rate → distortion domain
    "R14": ("distortion",  80),   # signal beyond closed-loop BW
    "R15": ("distortion",  50),   # insufficient GBW margin
    "R16": ("distortion", 100),   # slew-rate limiting
    "R17": ("distortion",  60),   # marginal slew rate
    "R18": ("distortion",  40),   # BW < 20 kHz
    "R19": ("stability",   35),   # integrator DC saturation

    # Stability
    "R20": ("stability",  100),   # phase margin < 30° — oscillation
    "R21": ("stability",   65),   # phase margin 30–45°
    "R22": ("stability",   55),   # high gain + low GBW
    "R23": ("stability",   70),   # comparator without hysteresis
    "R24": ("stability",   40),   # feedback factor too small
    "R25": ("stability",    5),   # info: unity-gain check

    # Noise
    "R26": ("noise",       90),   # output noise > 1 mVrms
    "R27": ("noise",       60),   # high input noise density
    "R28": ("noise",       35),   # thermal noise floor
    "R29": ("noise",       75),   # reported hum / ground loop
    "R30": ("noise",       55),   # low CMRR device with hum

    # Ground Loop → noise domain
    "R31": ("noise",       70),   # ground loop circulating current
    "R32": ("noise",       40),   # low supply worsens ground loop
    "R33": ("noise",        0),   # AD8221 info — no penalty

    # Thermal → power domain (thermal limits headroom)
    "R34": ("power",       40),   # Pd > 500 mW
    "R35": ("power",       30),   # elevated output stage dissipation
    "R36": ("power",       25),   # LM741 thermal stress

    # Signal integrity
    "R37": ("distortion",  45),   # CM range violation
    "R38": ("power",       80),   # no output despite valid expected
}


# ─────────────────────────────────────────────────────────────────────────────
# Typed result
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class DomainScore:
    """
    Per-domain breakdown of penalty and resulting sub-score.

    Attributes
    ----------
    domain              : domain name
    weight              : fractional weight applied to this domain (0–1)
    raw_penalty         : raw penalty 0–100 before weighting
    weighted_penalty    : raw_penalty × weight  (actual points deducted)
    sub_score           : 100 − raw_penalty  (domain health, 0–100)
    risk_level          : 'Low' | 'Medium' | 'High' | 'Critical'
    contributing_rules  : list of rule IDs that contributed to this penalty
    penalty_trace       : list of human-readable trace strings
    """
    domain:              str
    weight:              float
    raw_penalty:         float
    weighted_penalty:    float
    sub_score:           float
    risk_level:          str
    contributing_rules:  list[str]
    penalty_trace:       list[str]


@dataclass
class CircuitReliabilityResult:
    """
    Full Circuit Reliability Assessment output.
    Every number is traceable to an engineering rule or formula.

    Attributes
    ----------
    reliability_score    : final 0–100 integer — the Circuit Reliability Score
    classification       : 'Excellent' | 'Good' | 'Fair' | 'Poor' | 'Critical'
    classification_color : hex colour for UI rendering
    total_weighted_penalty: sum of all weighted domain penalties
    power_margin         : DomainScore for Power Margin domain
    stability            : DomainScore for Stability domain
    noise                : DomainScore for Noise domain
    distortion           : DomainScore for Distortion domain
    primary_risk_domain  : the domain with the highest raw penalty
    triggered_rule_count : total rules that fired
    critical_rules       : list of critical-severity TriggeredRule IDs
    score_formula        : human-readable formula string with actual values

    Dashboard labels
    ----------------
    reliability_score    → "Circuit Reliability Score"
    power_margin         → "Power Margin"
    stability            → "Stability"
    noise                → "Noise"
    distortion           → "Distortion"

    PDF report labels
    -----------------
    Section heading      → "Circuit Reliability Assessment"
    Score line           → "Circuit Reliability Score: {reliability_score}/100"
    """
    reliability_score:     int
    classification:        str
    classification_color:  str
    total_weighted_penalty: float
    power_margin:          DomainScore
    stability:             DomainScore
    noise:                 DomainScore
    distortion:            DomainScore
    primary_risk_domain:   str
    triggered_rule_count:  int
    critical_rules:        list[str]
    score_formula:         str


# Backward-compatible alias — existing imports of HealthScoreResult still work
HealthScoreResult = CircuitReliabilityResult


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def compute_reliability_score(rule_result: RuleEvaluationResult) -> CircuitReliabilityResult:
    """
    Compute the Circuit Reliability Score from a fully evaluated RuleEvaluationResult.

    This is the primary public API of the Circuit Reliability Assessment Engine.

    Parameters
    ----------
    rule_result : output of app.engine.rule_engine.evaluate()

    Returns
    -------
    CircuitReliabilityResult
        .reliability_score  → displayed as "Circuit Reliability Score" on dashboard and PDF
    """
    calc     = rule_result.calculations
    triggered = rule_result.triggered_rules

    logger.info(
        "Health score: computing from %d triggered rules for %s/%s",
        len(triggered), calc.circuit_type, calc.op_amp_model,
    )

    # ── Step 1: accumulate rule-based penalties per domain ──────────────────
    domain_raw: dict[str, float]       = {"power": 0.0, "stability": 0.0,
                                           "noise": 0.0, "distortion": 0.0}
    domain_rules: dict[str, list[str]] = {"power": [], "stability": [],
                                           "noise": [], "distortion": []}
    domain_trace: dict[str, list[str]] = {"power": [], "stability": [],
                                           "noise": [], "distortion": []}

    for rule in triggered:
        mapping = _RULE_PENALTIES.get(rule.rule_id)
        if mapping is None:
            continue
        domain, penalty = mapping
        if penalty == 0:
            continue

        # Accumulate — cap each domain at 100 after adding
        prev = domain_raw[domain]
        domain_raw[domain] = min(100.0, prev + penalty)
        domain_rules[domain].append(rule.rule_id)
        domain_trace[domain].append(
            f"{rule.rule_id} ({rule.severity}): +{penalty:.0f} pts "
            f"[{rule.fault_name}]"
        )

    # ── Step 2: continuous engineering penalties (rule-independent) ──────────
    # These add penalty based on how bad the numeric values are,
    # even if no explicit rule fired (fine-grained gradient scoring).

    _apply_continuous_penalties(calc, domain_raw, domain_trace)

    # Cap each domain at 100
    for d in domain_raw:
        domain_raw[d] = min(100.0, max(0.0, domain_raw[d]))

    # ── Step 3: compute weighted total penalty ───────────────────────────────
    w = {
        "power":      WEIGHT_POWER_MARGIN,
        "stability":  WEIGHT_STABILITY,
        "noise":      WEIGHT_NOISE,
        "distortion": WEIGHT_DISTORTION,
    }

    total_penalty = sum(domain_raw[d] * w[d] for d in domain_raw)
    total_penalty = min(100.0, total_penalty)

    # ── Step 4: final score ──────────────────────────────────────────────────
    raw_score    = 100.0 - total_penalty
    reliability_score = int(round(max(0.0, min(100.0, raw_score))))

    # ── Step 5: build per-domain DomainScore objects ─────────────────────────
    domain_scores: dict[str, DomainScore] = {}
    for d, weight in w.items():
        raw  = domain_raw[d]
        wp   = raw * weight
        sub  = 100.0 - raw
        domain_scores[d] = DomainScore(
            domain=d.replace("_", " ").title(),
            weight=weight,
            raw_penalty=round(raw, 2),
            weighted_penalty=round(wp, 2),
            sub_score=round(sub, 2),
            risk_level=_classify_risk(raw),
            contributing_rules=domain_rules[d],
            penalty_trace=domain_trace[d],
        )

    # ── Step 6: classification ───────────────────────────────────────────────
    classification, color = _classify_score(reliability_score)

    # ── Step 7: primary risk domain ─────────────────────────────────────────
    primary_domain = max(domain_raw, key=lambda d: domain_raw[d])

    # ── Step 8: critical rule IDs ────────────────────────────────────────────
    critical_ids = [r.rule_id for r in triggered if r.severity == "critical"]

    # ── Step 9: formula string ───────────────────────────────────────────────
    formula = _build_formula_string(domain_raw, w, total_penalty, reliability_score)

    logger.info(
        "Circuit Reliability Score: %d (%s) | Power: %.0f | Stability: %.0f | "
        "Noise: %.0f | Distortion: %.0f | Total penalty: %.2f",
        reliability_score, classification,
        domain_raw["power"], domain_raw["stability"],
        domain_raw["noise"], domain_raw["distortion"],
        total_penalty,
    )

    return CircuitReliabilityResult(
        reliability_score=reliability_score,
        classification=classification,
        classification_color=color,
        total_weighted_penalty=round(total_penalty, 2),
        power_margin=domain_scores["power"],
        stability=domain_scores["stability"],
        noise=domain_scores["noise"],
        distortion=domain_scores["distortion"],
        primary_risk_domain=primary_domain.replace("_", " ").title(),
        triggered_rule_count=len(triggered),
        critical_rules=critical_ids,
        score_formula=formula,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Continuous penalty functions
# Applied independently of rule triggers — gradient scoring
# ─────────────────────────────────────────────────────────────────────────────
def _apply_continuous_penalties(
    calc: CircuitCalculations,
    domain_raw: dict[str, float],
    domain_trace: dict[str, list[str]],
) -> None:
    """
    Add fine-grained penalty contributions based on numeric engineering metrics.
    Each function is a monotonic mapping: worse value → higher penalty increment.
    """

    # ── Power: headroom ratio ────────────────────────────────────────────────
    # headroom_ratio = headroom / supply_v
    # < 0 (saturated)  → +40 pts
    # 0–0.10           → +25 pts (critically low)
    # 0.10–0.20        → +12 pts (low)
    # 0.20–0.35        → + 5 pts (marginal)
    # ≥ 0.35           →   0 pts (healthy)
    hr = calc.headroom_ratio
    if hr < 0:
        p, label = 40, f"Headroom ratio {hr:.3f} (saturated) → +40 pts [Power]"
    elif hr < 0.10:
        p, label = 25, f"Headroom ratio {hr:.3f} (<10%) → +25 pts [Power]"
    elif hr < 0.20:
        p, label = 12, f"Headroom ratio {hr:.3f} (<20%) → +12 pts [Power]"
    elif hr < 0.35:
        p, label =  5, f"Headroom ratio {hr:.3f} (<35%) → +5 pts [Power]"
    else:
        p, label =  0, ""
    if p > 0:
        domain_raw["power"] = min(100.0, domain_raw["power"] + p)
        domain_trace["power"].append(label)

    # ── Stability: phase margin ──────────────────────────────────────────────
    pm = calc.phase_margin_deg
    if pm < 20:
        p, label = 50, f"Phase margin {pm:.1f}° (<20°) → +50 pts [Stability]"
    elif pm < 30:
        p, label = 35, f"Phase margin {pm:.1f}° (<30°) → +35 pts [Stability]"
    elif pm < 45:
        p, label = 20, f"Phase margin {pm:.1f}° (<45°) → +20 pts [Stability]"
    elif pm < 60:
        p, label = 8,  f"Phase margin {pm:.1f}° (<60°) → +8 pts [Stability]"
    else:
        p, label = 0, ""
    if p > 0:
        domain_raw["stability"] = min(100.0, domain_raw["stability"] + p)
        domain_trace["stability"].append(label)

    # ── Stability: slew-rate ratio ────────────────────────────────────────────
    # sr_ratio = signal_freq / slew_rate_limit_hz
    if calc.slew_rate_limit_hz > 0:
        sr_ratio = calc.signal_freq_hz / calc.slew_rate_limit_hz
        if sr_ratio > 1.5:
            p, label = 30, f"Slew ratio {sr_ratio:.2f} (>1.5×) → +30 pts [Distortion]"
        elif sr_ratio > 1.0:
            p, label = 20, f"Slew ratio {sr_ratio:.2f} (>1.0×) → +20 pts [Distortion]"
        elif sr_ratio > 0.5:
            p, label = 8,  f"Slew ratio {sr_ratio:.2f} (>0.5×) → +8 pts [Distortion]"
        else:
            p, label = 0, ""
        if p > 0:
            domain_raw["distortion"] = min(100.0, domain_raw["distortion"] + p)
            domain_trace["distortion"].append(label)

    # ── Noise: output noise µVrms ─────────────────────────────────────────────
    # Professional audio noise floor target: < 10 µVrms
    on = calc.output_noise_uv_rms
    if on > 1000:
        p, label = 40, f"Output noise {on:.0f} µVrms (>1000) → +40 pts [Noise]"
    elif on > 200:
        p, label = 25, f"Output noise {on:.0f} µVrms (>200) → +25 pts [Noise]"
    elif on > 50:
        p, label = 15, f"Output noise {on:.0f} µVrms (>50) → +15 pts [Noise]"
    elif on > 10:
        p, label =  6, f"Output noise {on:.0f} µVrms (>10) → +6 pts [Noise]"
    else:
        p, label =  0, ""
    if p > 0:
        domain_raw["noise"] = min(100.0, domain_raw["noise"] + p)
        domain_trace["noise"].append(label)

    # ── Noise: GBW margin ratio ───────────────────────────────────────────────
    # Frequency response flatness: f_signal should be << closed-loop BW
    if calc.signal_freq_hz > 0 and calc.closed_loop_bw_hz > 0:
        f_ratio = calc.signal_freq_hz / calc.closed_loop_bw_hz
        if f_ratio > 1.0:
            p, label = 25, f"f/BW ratio {f_ratio:.2f} (>1.0 — beyond BW) → +25 pts [Distortion]"
        elif f_ratio > 0.5:
            p, label = 12, f"f/BW ratio {f_ratio:.2f} (>0.5) → +12 pts [Distortion]"
        elif f_ratio > 0.1:
            p, label =  4, f"f/BW ratio {f_ratio:.2f} (>0.1) → +4 pts [Distortion]"
        else:
            p, label =  0, ""
        if p > 0:
            domain_raw["distortion"] = min(100.0, domain_raw["distortion"] + p)
            domain_trace["distortion"].append(label)

    # ── Distortion: DC offset error ───────────────────────────────────────────
    vos = calc.vos_output_error_mv
    if vos > 500:
        p, label = 30, f"Vos output error {vos:.1f} mV (>500) → +30 pts [Distortion]"
    elif vos > 100:
        p, label = 15, f"Vos output error {vos:.1f} mV (>100) → +15 pts [Distortion]"
    elif vos > 50:
        p, label =  8, f"Vos output error {vos:.1f} mV (>50) → +8 pts [Distortion]"
    elif vos > 20:
        p, label =  3, f"Vos output error {vos:.1f} mV (>20) → +3 pts [Distortion]"
    else:
        p, label =  0, ""
    if p > 0:
        domain_raw["distortion"] = min(100.0, domain_raw["distortion"] + p)
        domain_trace["distortion"].append(label)


# ─────────────────────────────────────────────────────────────────────────────
# Classification helpers
# ─────────────────────────────────────────────────────────────────────────────
def _classify_score(score: int) -> tuple[str, str]:
    """Map Circuit Reliability Score integer to (classification label, hex colour)."""
    if score >= 90:
        return "Excellent", "#16a34a"   # green
    if score >= 75:
        return "Good",      "#65a30d"   # lime
    if score >= 55:
        return "Fair",      "#ca8a04"   # amber
    if score >= 35:
        return "Poor",      "#dc2626"   # red
    return "Critical",      "#7f1d1d"   # dark red


def _classify_risk(raw_penalty: float) -> str:
    """Map a domain raw penalty (0–100) to a risk label."""
    if raw_penalty >= 75:
        return "Critical"
    if raw_penalty >= 50:
        return "High"
    if raw_penalty >= 25:
        return "Medium"
    if raw_penalty > 0:
        return "Low"
    return "None"


# ─────────────────────────────────────────────────────────────────────────────
# Formula string builder
# ─────────────────────────────────────────────────────────────────────────────
def _build_formula_string(
    domain_raw:    dict[str, float],
    weights:       dict[str, float],
    total_penalty: float,
    score:         int,
) -> str:
    pw  = domain_raw["power"]      * weights["power"]
    sw  = domain_raw["stability"]  * weights["stability"]
    nw  = domain_raw["noise"]      * weights["noise"]
    dw  = domain_raw["distortion"] * weights["distortion"]
    return (
        f"Circuit Reliability Score = 100 − Total Weighted Penalty\n"
        f"= 100 − (Power×{weights['power']} + Stability×{weights['stability']} "
        f"+ Noise×{weights['noise']} + Distortion×{weights['distortion']})\n"
        f"= 100 − ({pw:.2f} + {sw:.2f} + {nw:.2f} + {dw:.2f})\n"
        f"= 100 − {total_penalty:.2f}\n"
        f"= {score}"
    )
