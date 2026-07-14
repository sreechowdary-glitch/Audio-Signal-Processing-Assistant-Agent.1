"""
circuit_calculator.py
---------------------
Pure engineering formula library for audio op-amp circuits.

All functions are stateless and dependency-free (stdlib + math only).
They are called by both rule_engine.py and health_score.py (Circuit Reliability Assessment Engine).

Covered calculations
--------------------
- Expected output voltage (non-inverting, inverting, difference, integrator)
- Output swing headroom vs supply rail
- Gain-Bandwidth Product (GBW) violation check
- Slew rate limiting frequency
- Input noise referred to output (en × gain)
- Thermal noise voltage (Johnson-Nyquist)
- CMRR impact on output error
- Closed-loop bandwidth
- Power dissipation estimate
- Feedback factor (Beta)
- Noise gain
- Phase margin estimate (dominant-pole model)
"""

import math
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Op-amp datasheet parameters (used by calculator and rules)
# Known devices: LM358, LM741, TL071, NE5532, LM386, OPA2134, AD8221
# ---------------------------------------------------------------------------
OP_AMP_SPECS: dict[str, dict] = {
    "LM358": {
        "gbw_hz":        1.0e6,      # 1 MHz unity-gain bandwidth
        "slew_rate_vus": 0.5,        # 0.5 V/µs
        "vos_mv":        7.0,        # input offset voltage (max, mV)
        "ib_na":         250.0,      # input bias current (max, nA)
        "en_nv_hz":      40.0,       # input voltage noise density (nV/√Hz)
        "in_pa_hz":      1000.0,     # input current noise (pA/√Hz)
        "cmrr_db":       65.0,       # common-mode rejection ratio (min, dB)
        "psrr_db":       65.0,       # power supply rejection ratio (dB)
        "max_supply_v":  16.0,       # max single-supply voltage
        "min_supply_v":  3.0,        # min single-supply voltage
        "output_swing_margin_v": 1.5, # headroom from rail to max swing (V)
        "is_rail_to_rail": False,
        "notes": "Low-power, general purpose. Not recommended above ~100 kHz."
    },
    "LM741": {
        "gbw_hz":        1.0e6,
        "slew_rate_vus": 0.5,
        "vos_mv":        6.0,
        "ib_na":         500.0,
        "en_nv_hz":      20.0,
        "in_pa_hz":      500.0,
        "cmrr_db":       70.0,
        "psrr_db":       77.0,
        "max_supply_v":  22.0,
        "min_supply_v":  10.0,       # requires split supply ≥ ±5V
        "output_swing_margin_v": 2.0,
        "is_rail_to_rail": False,
        "notes": "Classic general purpose. High input offset. Avoid for precision or audio."
    },
    "TL071": {
        "gbw_hz":        3.0e6,
        "slew_rate_vus": 13.0,
        "vos_mv":        10.0,
        "ib_na":         0.065,      # JFET input — very low bias current
        "en_nv_hz":      18.0,
        "in_pa_hz":      0.01,
        "cmrr_db":       86.0,
        "psrr_db":       86.0,
        "max_supply_v":  18.0,
        "min_supply_v":  7.0,
        "output_swing_margin_v": 1.5,
        "is_rail_to_rail": False,
        "notes": "JFET input, good audio op-amp. Better slew rate than LM358/741."
    },
    "NE5532": {
        "gbw_hz":        10.0e6,
        "slew_rate_vus": 9.0,
        "vos_mv":        4.0,
        "ib_na":         200.0,
        "en_nv_hz":      5.0,        # very low noise — audiophile grade
        "in_pa_hz":      700.0,
        "cmrr_db":       100.0,
        "psrr_db":       100.0,
        "max_supply_v":  22.0,
        "min_supply_v":  10.0,
        "output_swing_margin_v": 1.5,
        "is_rail_to_rail": False,
        "notes": "Low-noise audio op-amp. Industry standard for studio equipment."
    },
    "LM386": {
        "gbw_hz":        0.3e6,      # low-power audio amp
        "slew_rate_vus": 0.3,
        "vos_mv":        2.0,
        "ib_na":         250.0,
        "en_nv_hz":      50.0,
        "in_pa_hz":      1500.0,
        "cmrr_db":       55.0,
        "psrr_db":       50.0,
        "max_supply_v":  12.0,
        "min_supply_v":  4.0,
        "output_swing_margin_v": 1.0,
        "is_rail_to_rail": False,
        "notes": "Low-voltage audio power amplifier. Fixed gain 20–200."
    },
    "OPA2134": {
        "gbw_hz":        8.0e6,
        "slew_rate_vus": 20.0,
        "vos_mv":        0.05,       # precision audio op-amp
        "ib_na":         0.005,
        "en_nv_hz":      8.0,
        "in_pa_hz":      0.003,
        "cmrr_db":       100.0,
        "psrr_db":       100.0,
        "max_supply_v":  18.0,
        "min_supply_v":  4.5,
        "output_swing_margin_v": 1.0,
        "is_rail_to_rail": False,
        "notes": "SoundPlus precision audio op-amp. Excellent for DAC output stages."
    },
    "AD8221": {
        "gbw_hz":        1.0e6,
        "slew_rate_vus": 1.2,
        "vos_mv":        0.025,
        "ib_na":         2.0,
        "en_nv_hz":      7.0,
        "in_pa_hz":      40.0,
        "cmrr_db":       130.0,     # instrumentation amp — very high CMRR
        "psrr_db":       100.0,
        "max_supply_v":  18.0,
        "min_supply_v":  4.6,
        "output_swing_margin_v": 0.1,
        "is_rail_to_rail": True,
        "notes": "Precision instrumentation amplifier. Not a standard op-amp topology."
    },
    "GENERIC": {
        "gbw_hz":        1.0e6,
        "slew_rate_vus": 1.0,
        "vos_mv":        5.0,
        "ib_na":         100.0,
        "en_nv_hz":      20.0,
        "in_pa_hz":      200.0,
        "cmrr_db":       70.0,
        "psrr_db":       70.0,
        "max_supply_v":  18.0,
        "min_supply_v":  5.0,
        "output_swing_margin_v": 1.5,
        "is_rail_to_rail": False,
        "notes": "Generic fallback parameters. Replace with device datasheet values."
    },
}

# ---------------------------------------------------------------------------
# Dataclass for calculated circuit quantities
# ---------------------------------------------------------------------------
@dataclass
class CircuitCalculations:
    """All computed engineering values for a given circuit parameter set."""
    # Input parameters (mirrored for convenience)
    circuit_type:       str
    op_amp_model:       str
    supply_voltage_v:   float
    gain:               float
    input_signal_mv:    float
    observed_issue:     str
    signal_freq_hz:     float

    # Computed quantities
    expected_output_v:       float   # V_out = (V_in_mV/1000) × gain
    output_swing_max_v:      float   # supply_v − swing_margin
    output_headroom_v:       float   # output_swing_max − |expected_output|
    headroom_ratio:          float   # headroom / supply_v  (0–1)
    gbw_hz:                  float   # device GBW from datasheet
    closed_loop_bw_hz:       float   # GBW / noise_gain
    noise_gain:              float   # 1 + R_f/R_in  (= gain for non-inv)
    feedback_factor:         float   # 1 / noise_gain
    slew_rate_vus:           float   # device slew rate (V/µs)
    slew_rate_limit_hz:      float   # f_SR = SR / (2π × V_peak)
    slew_rate_headroom_hz:   float   # slew_rate_limit - signal_freq
    gbw_margin_hz:           float   # closed_loop_bw - signal_freq
    phase_margin_deg:        float   # estimated dominant-pole phase margin
    output_noise_uv_rms:     float   # en × gain × √(BW), µVrms
    thermal_noise_uv_rms:    float   # 4kTR × √BW at 1 kΩ source, µVrms
    vos_output_error_mv:     float   # Vos × gain referred to output
    power_dissipation_mw:    float   # rough static estimate
    input_referred_noise_nv: float   # en_nv_hz (device noise floor)


def get_op_amp_specs(model: str) -> dict:
    """Return datasheet specs for op_amp_model, falling back to GENERIC."""
    key = model.upper().replace(" ", "")
    return OP_AMP_SPECS.get(key, OP_AMP_SPECS["GENERIC"])


def compute_all(params: dict) -> CircuitCalculations:
    """
    Compute all engineering quantities from circuit parameter dict.

    Parameters
    ----------
    params : dict with keys:
        circuit_type    : str  ('non_inverting' | 'inverting' | 'difference' |
                                'integrator'   | 'comparator')
        op_amp_model    : str  ('LM358' | 'TL071' | …)
        supply_voltage_v: float  (positive magnitude; ±5V → 5.0)
        gain            : float
        input_signal_mv : float  (millivolts)
        observed_issue  : str
        signal_freq_hz  : float  (default 1000.0 if not provided)
    """
    ct     = params.get("circuit_type", "non_inverting")
    model  = params.get("op_amp_model", "GENERIC")
    Vs     = abs(float(params.get("supply_voltage_v", 5.0)))
    gain   = abs(float(params.get("gain", 1.0)))
    Vin_mv = float(params.get("input_signal_mv", 100.0))
    issue  = params.get("observed_issue", "none")
    f_sig  = float(params.get("signal_freq_hz", 1000.0))

    specs  = get_op_amp_specs(model)

    # -- Output voltage ------------------------------------------------------
    Vin_v = Vin_mv / 1000.0
    if ct == "inverting":
        expected_output_v = -(Vin_v * gain)
    else:
        expected_output_v = Vin_v * gain       # non-inverting / difference

    swing_margin    = specs["output_swing_margin_v"]
    swing_max       = Vs - swing_margin if not specs["is_rail_to_rail"] else Vs
    abs_expected    = abs(expected_output_v)
    headroom        = swing_max - abs_expected
    headroom_ratio  = headroom / Vs if Vs > 0 else 0.0

    # -- Noise gain & feedback -----------------------------------------------
    if ct == "non_inverting":
        noise_gain       = gain
        feedback_factor  = 1.0 / gain if gain > 0 else 1.0
    elif ct == "inverting":
        noise_gain       = 1.0 + gain   # noise gain is always > signal gain for inverter
        feedback_factor  = 1.0 / noise_gain
    else:
        noise_gain       = gain
        feedback_factor  = 1.0 / gain if gain > 0 else 1.0

    # -- Bandwidth -----------------------------------------------------------
    gbw_hz           = specs["gbw_hz"]
    closed_loop_bw   = gbw_hz / max(noise_gain, 1.0)
    gbw_margin       = closed_loop_bw - f_sig

    # -- Slew rate -----------------------------------------------------------
    sr_vus           = specs["slew_rate_vus"]
    sr_vs            = sr_vus * 1e6           # V/s
    V_peak           = abs_expected if abs_expected > 0 else Vin_v
    if V_peak > 0:
        slew_limit_hz = sr_vs / (2.0 * math.pi * V_peak)
    else:
        slew_limit_hz = sr_vs / (2.0 * math.pi * 1e-3)  # degenerate guard
    slew_headroom_hz = slew_limit_hz - f_sig

    # -- Phase margin (single dominant-pole model) ---------------------------
    # At closed-loop BW, phase shift ≈ 90° (one pole); margin = 90° − arctan(f/f_cross)
    # Simplified: assume dominant pole at GBW/noise_gain
    if f_sig > 0 and closed_loop_bw > 0:
        phase_margin = 90.0 - math.degrees(math.atan(f_sig / closed_loop_bw))
        phase_margin = max(0.0, phase_margin)
    else:
        phase_margin = 90.0

    # -- Noise referred to output --------------------------------------------
    # en_out = en_device × noise_gain × √(BW)  in µVrms
    en_nv_hz     = specs["en_nv_hz"]
    bw_for_noise = max(closed_loop_bw, 1.0)
    output_noise = en_nv_hz * 1e-9 * noise_gain * math.sqrt(bw_for_noise)
    output_noise_uv = output_noise * 1e6   # µVrms

    # -- Thermal noise (Johnson-Nyquist) at 1 kΩ source resistance ----------
    k   = 1.380649e-23    # Boltzmann constant
    T   = 300.15          # 27 °C in Kelvin
    R   = 1000.0          # 1 kΩ representative source impedance
    thermal_v = math.sqrt(4 * k * T * R * bw_for_noise)
    thermal_uv = thermal_v * 1e6  # µVrms

    # -- Offset voltage referred to output -----------------------------------
    vos_mv   = specs["vos_mv"]
    vos_out  = vos_mv * noise_gain   # mV × noise_gain

    # -- Static power dissipation estimate -----------------------------------
    # Very rough: Iq × Vs; Iq assumed ≈ 1 mA (typical for general-purpose)
    Iq_ma = 1.0
    power_mw = Iq_ma * (2.0 * Vs)   # dual supply

    return CircuitCalculations(
        circuit_type=ct,
        op_amp_model=model,
        supply_voltage_v=Vs,
        gain=gain,
        input_signal_mv=Vin_mv,
        observed_issue=issue,
        signal_freq_hz=f_sig,
        expected_output_v=round(expected_output_v, 4),
        output_swing_max_v=round(swing_max, 4),
        output_headroom_v=round(headroom, 4),
        headroom_ratio=round(headroom_ratio, 4),
        gbw_hz=gbw_hz,
        closed_loop_bw_hz=round(closed_loop_bw, 2),
        noise_gain=round(noise_gain, 4),
        feedback_factor=round(feedback_factor, 6),
        slew_rate_vus=sr_vus,
        slew_rate_limit_hz=round(slew_limit_hz, 2),
        slew_rate_headroom_hz=round(slew_headroom_hz, 2),
        gbw_margin_hz=round(gbw_margin, 2),
        phase_margin_deg=round(phase_margin, 2),
        output_noise_uv_rms=round(output_noise_uv, 4),
        thermal_noise_uv_rms=round(thermal_uv, 4),
        vos_output_error_mv=round(vos_out, 4),
        power_dissipation_mw=round(power_mw, 2),
        input_referred_noise_nv=round(en_nv_hz, 2),
    )
