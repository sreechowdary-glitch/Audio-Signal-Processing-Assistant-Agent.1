"""
rules_library.py
----------------
38 engineering rule definitions for audio op-amp circuit diagnostics.

Each rule is a RuleDefinition dataclass containing:
  - rule_id         : unique identifier  (R01 … R38)
  - category        : fault category string
  - severity        : 'critical' | 'high' | 'medium' | 'low' | 'info'
  - condition       : callable(CircuitCalculations) → bool
  - fault_name      : short fault label
  - root_cause      : engineering root-cause explanation string
  - corrective_actions : list of actionable fix strings
  - engineering_detail : callable(CircuitCalculations) → str  (numeric context)

Design rules
------------
- No AI calls, no I/O, no state.
- All numeric thresholds are grounded in datasheet limits and standard
  audio electronics practice (Horowitz & Hill; Texas Instruments AN-31).
- Each condition is a pure function of CircuitCalculations.
"""

from dataclasses import dataclass, field
from typing import Callable
from app.engine.circuit_calculator import CircuitCalculations

# ---------------------------------------------------------------------------
# Rule dataclass
# ---------------------------------------------------------------------------
@dataclass
class RuleDefinition:
    rule_id:             str
    category:            str
    severity:            str                         # critical/high/medium/low/info
    condition:           Callable[[CircuitCalculations], bool]
    fault_name:          str
    root_cause:          str
    corrective_actions:  list[str]
    engineering_detail:  Callable[[CircuitCalculations], str] = field(
        default=lambda c: "", repr=False
    )


# ---------------------------------------------------------------------------
# Rule registry — evaluated in order by rule_engine.py
# ---------------------------------------------------------------------------
RULES: list[RuleDefinition] = []

def _r(rule: RuleDefinition) -> RuleDefinition:
    RULES.append(rule)
    return rule


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY A — OUTPUT SATURATION & SUPPLY RAIL (R01–R07)
# ═══════════════════════════════════════════════════════════════════════════

_r(RuleDefinition(
    rule_id="R01",
    category="Output Saturation",
    severity="critical",
    condition=lambda c: abs(c.expected_output_v) >= c.supply_voltage_v,
    fault_name="Output Rail Saturation",
    root_cause=(
        "The expected output voltage equals or exceeds the supply rail voltage. "
        "The op-amp output stage is sourcing/sinking current against the rail, "
        "causing hard saturation. Output will be rail-clamped (supply_v − Vce_sat)."
    ),
    corrective_actions=[
        "Reduce gain so that: gain ≤ (supply_voltage_v × 0.9) / (input_signal_mv / 1000)",
        "Increase supply voltage to at least |expected_output| × 1.2",
        "Reduce input signal amplitude to stay within linear range",
        "Add an input attenuator before the op-amp stage",
    ],
    engineering_detail=lambda c: (
        f"Expected output {abs(c.expected_output_v):.3f} V ≥ supply rail {c.supply_voltage_v:.1f} V. "
        f"Headroom: {c.output_headroom_v:.3f} V. Gain needs to be ≤ {c.output_swing_max_v / (c.input_signal_mv / 1000):.1f} for this supply."
    ),
))

_r(RuleDefinition(
    rule_id="R02",
    category="Output Saturation",
    severity="high",
    condition=lambda c: (
        abs(c.expected_output_v) >= c.output_swing_max_v and
        abs(c.expected_output_v) < c.supply_voltage_v
    ),
    fault_name="Output Swing Limit Exceeded",
    root_cause=(
        "The expected output exceeds the op-amp's guaranteed output swing maximum "
        "(supply − swing_margin). The output will clip at the device's swing limit, "
        "not at the full supply rail. This is device-specific clipping, not a supply fault."
    ),
    corrective_actions=[
        "Reduce gain by at least the headroom deficit",
        "Use a rail-to-rail output op-amp (e.g., MCP6002, LMV358) if full-swing output is required",
        "Increase supply voltage to provide additional output headroom",
    ],
    engineering_detail=lambda c: (
        f"Expected {abs(c.expected_output_v):.3f} V > swing max {c.output_swing_max_v:.3f} V "
        f"(supply {c.supply_voltage_v:.1f} V − margin {c.supply_voltage_v - c.output_swing_max_v:.1f} V). "
        f"Deficit: {abs(c.output_headroom_v):.3f} V."
    ),
))

_r(RuleDefinition(
    rule_id="R03",
    category="Output Saturation",
    severity="high",
    condition=lambda c: 0 < c.output_headroom_v < (c.supply_voltage_v * 0.10),
    fault_name="Critically Low Output Headroom",
    root_cause=(
        "Output headroom is less than 10% of supply voltage. Even small increases in "
        "input signal amplitude, supply tolerance (±5–10%), or temperature drift will "
        "push the output into saturation. The circuit is operating dangerously close to its limit."
    ),
    corrective_actions=[
        "Target minimum 20% headroom: reduce gain or input signal",
        "Verify supply voltage regulation — a drooping supply reduces headroom further",
        "Add automatic gain control (AGC) if input amplitude varies",
    ],
    engineering_detail=lambda c: (
        f"Headroom: {c.output_headroom_v:.3f} V = {c.headroom_ratio * 100:.1f}% of supply. "
        f"Safe minimum is 10–20% ({c.supply_voltage_v * 0.1:.2f}–{c.supply_voltage_v * 0.2:.2f} V)."
    ),
))

_r(RuleDefinition(
    rule_id="R04",
    category="Supply Rail",
    severity="critical",
    condition=lambda c: c.supply_voltage_v < 3.0,
    fault_name="Supply Voltage Below Absolute Minimum",
    root_cause=(
        "Supply voltage is below 3.0 V — below the minimum operating voltage of most "
        "general-purpose op-amps (LM358 requires ≥ 3V, LM741 requires ≥ 10V split supply). "
        "Device behavior is undefined; output may not swing at all."
    ),
    corrective_actions=[
        "Use a single-supply op-amp rated for this voltage (e.g., MCP6001 operates at 1.8 V)",
        "Increase supply voltage to meet device minimum specification",
        "Verify power supply output under load — measure at device pins, not at regulator",
    ],
    engineering_detail=lambda c: (
        f"Supply: {c.supply_voltage_v:.2f} V < 3.0 V absolute minimum."
    ),
))

_r(RuleDefinition(
    rule_id="R05",
    category="Supply Rail",
    severity="high",
    condition=lambda c: (
        c.op_amp_model.upper() in ["LM741", "LM741C"] and c.supply_voltage_v < 5.0
    ),
    fault_name="LM741 Below Minimum Supply",
    root_cause=(
        "The LM741 requires a minimum split supply of ±5V (10V total). "
        "Below this threshold, input common-mode range and output swing are undefined. "
        "The device cannot operate reliably at this supply voltage."
    ),
    corrective_actions=[
        "Increase supply to at least ±5V (10V split supply) for LM741",
        "Replace LM741 with a modern single-supply op-amp for low-voltage applications",
        "Use LM358 or TL071 for ±3V or higher single-supply operation",
    ],
    engineering_detail=lambda c: (
        f"LM741 minimum supply: ±5V. Current supply: ±{c.supply_voltage_v:.1f}V."
    ),
))

_r(RuleDefinition(
    rule_id="R06",
    category="Supply Rail",
    severity="medium",
    condition=lambda c: c.supply_voltage_v > 15.0,
    fault_name="Supply Voltage Approaching Absolute Maximum",
    root_cause=(
        "Supply voltage exceeds 15V — approaching the absolute maximum ratings of "
        "LM358 (18V), TL071 (18V), and LM741 (22V). Derating is standard practice; "
        "operating at 85%+ of Vmax reduces reliability and device lifetime."
    ),
    corrective_actions=[
        "Derate supply to ≤ 85% of device Vmax (≤ 15.3V for LM358, ≤ 15.3V for TL071)",
        "Add supply protection zener or TVS diode",
        "Verify supply voltage regulation and ripple at op-amp pins",
    ],
    engineering_detail=lambda c: (
        f"Supply: {c.supply_voltage_v:.1f} V. Standard derating: operate at ≤ 85% Vmax."
    ),
))

_r(RuleDefinition(
    rule_id="R07",
    category="Supply Rail",
    severity="medium",
    condition=lambda c: c.supply_voltage_v < 5.0 and c.gain > 20,
    fault_name="Low Supply with High Gain — Compressed Dynamic Range",
    root_cause=(
        "Running high gain on a low supply severely compresses the usable input dynamic range. "
        "Input amplitude range = (supply − swing_margin) / gain. At 3.3V supply and gain=50, "
        "max input is only 34 mV before clipping."
    ),
    corrective_actions=[
        "Calculate max input as: V_in_max = (V_supply − V_swing_margin) / gain",
        "Either reduce gain or increase supply voltage",
        "Add soft-clipping input limiter if dynamic range cannot be controlled",
    ],
    engineering_detail=lambda c: (
        f"Max linear input: {((c.supply_voltage_v - 1.5) / c.gain * 1000):.1f} mV "
        f"at gain={c.gain:.0f}, supply={c.supply_voltage_v:.1f}V."
    ),
))


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY B — GAIN MISCONFIGURATION (R08–R13)
# ═══════════════════════════════════════════════════════════════════════════

_r(RuleDefinition(
    rule_id="R08",
    category="Gain Misconfiguration",
    severity="high",
    condition=lambda c: c.gain > 100,
    fault_name="Gain Exceeds Practical Audio Limit",
    root_cause=(
        "Gain > 100 in a single op-amp stage is impractical for most audio applications. "
        "It consumes excessive GBW budget, amplifies offset voltage to a large DC error, "
        "and reduces closed-loop bandwidth significantly. Multi-stage designs are required."
    ),
    corrective_actions=[
        "Split high-gain designs into two cascaded stages (e.g., ×10 × ×10 = ×100)",
        "Use an instrumentation amplifier (INA128, AD8221) for high stable gain",
        "Add a high-pass filter to remove amplified DC offset",
        f"Maximum practical single-stage gain for most audio op-amps: 50–100×",
    ],
    engineering_detail=lambda c: (
        f"Configured gain: {c.gain:.0f}×. GBW consumed: {c.gbw_hz / c.noise_gain / 1000:.1f} kHz "
        f"closed-loop bandwidth."
    ),
))

_r(RuleDefinition(
    rule_id="R09",
    category="Gain Misconfiguration",
    severity="medium",
    condition=lambda c: c.gain > 50 and c.closed_loop_bw_hz < 20000,
    fault_name="Gain Limits Bandwidth Below Audio Range",
    root_cause=(
        "The closed-loop bandwidth (GBW / noise_gain) has fallen below 20 kHz — "
        "the upper limit of human hearing. The amplifier will roll off within the "
        "audio band, causing high-frequency attenuation and phase shift."
    ),
    corrective_actions=[
        "Reduce gain to restore bandwidth above 20 kHz",
        "Use a higher-GBW op-amp (NE5532 at 10 MHz, OPA2134 at 8 MHz)",
        "Compensate for in-band rolloff with EQ if gain cannot be reduced",
    ],
    engineering_detail=lambda c: (
        f"Closed-loop BW: {c.closed_loop_bw_hz:.0f} Hz < 20,000 Hz. "
        f"GBW: {c.gbw_hz / 1000:.0f} kHz ÷ noise_gain {c.noise_gain:.1f} = "
        f"{c.closed_loop_bw_hz:.0f} Hz."
    ),
))

_r(RuleDefinition(
    rule_id="R10",
    category="Gain Misconfiguration",
    severity="low",
    condition=lambda c: c.gain < 1.0 and c.circuit_type == "non_inverting",
    fault_name="Non-Inverting Gain Below Unity — Impossible Configuration",
    root_cause=(
        "A non-inverting op-amp amplifier cannot have gain below 1.0 with resistive feedback. "
        "The minimum stable gain is 1 (unity gain / voltage follower). "
        "A gain < 1 implies incorrect resistor calculation or circuit topology."
    ),
    corrective_actions=[
        "Set R_f = 0 (short) and R_in = open for unity-gain voltage follower",
        "If attenuation is required, use a resistor divider before a unity-gain buffer",
        "Verify gain formula: G = 1 + R_f/R_in ≥ 1 always",
    ],
    engineering_detail=lambda c: (
        f"Configured gain: {c.gain:.3f}. Non-inverting minimum gain = 1.0."
    ),
))

_r(RuleDefinition(
    rule_id="R11",
    category="Gain Misconfiguration",
    severity="medium",
    condition=lambda c: c.vos_output_error_mv > 50.0,
    fault_name="DC Offset Error Due to Input Offset Voltage × Gain",
    root_cause=(
        "The input offset voltage (Vos) multiplied by the noise gain produces a "
        "significant DC error at the output. This DC component clips headroom, "
        "can damage DC-coupled loads, and causes audible thumps on power-up."
    ),
    corrective_actions=[
        "Add a DC-blocking capacitor at the output (C = 1/(2π×f_3dB×R_load))",
        "Use an op-amp with lower Vos (NE5532: 4 mV, OPA2134: 0.05 mV)",
        "Add offset nulling if available on the device (LM741 has offset null pins)",
        "Reduce noise gain to lower the output-referred offset error",
    ],
    engineering_detail=lambda c: (
        f"Vos_out = {c.vos_output_error_mv:.2f} mV "
        f"(device Vos × noise_gain = Vos × {c.noise_gain:.1f}). "
        f"Threshold: 50 mV."
    ),
))

_r(RuleDefinition(
    rule_id="R12",
    category="Gain Misconfiguration",
    severity="info",
    condition=lambda c: c.gain == 1.0 and c.circuit_type == "non_inverting",
    fault_name="Unity Gain Buffer Configuration",
    root_cause=(
        "Circuit is configured as a unity-gain voltage follower (gain = 1). "
        "This is a valid configuration for impedance transformation. "
        "Ensure the op-amp is unity-gain stable (not all devices are)."
    ),
    corrective_actions=[
        "Verify the selected op-amp is unity-gain stable (check datasheet)",
        "LM741, TL071, NE5532 are unity-gain stable. Some high-speed op-amps are not.",
        "Add a small capacitor (10–100 pF) in the feedback path if oscillation occurs",
    ],
    engineering_detail=lambda c: (
        f"Unity gain buffer. Closed-loop BW = GBW = {c.gbw_hz / 1000:.0f} kHz."
    ),
))

_r(RuleDefinition(
    rule_id="R13",
    category="Gain Misconfiguration",
    severity="medium",
    condition=lambda c: (
        c.circuit_type == "inverting" and c.feedback_factor < 0.02
    ),
    fault_name="Inverting Configuration — Noise Gain Much Larger Than Signal Gain",
    root_cause=(
        "For an inverting amplifier, noise gain = 1 + signal_gain. At high gains, "
        "the noise gain significantly exceeds the signal gain, consuming GBW and "
        "amplifying noise at the input more than intended."
    ),
    corrective_actions=[
        "Consider a non-inverting topology if phase inversion is not required",
        "Use a T-feedback network to reduce noise gain for high-gain inverting stages",
        "Verify GBW budget: closed-loop BW = GBW / (1 + signal_gain)",
    ],
    engineering_detail=lambda c: (
        f"Signal gain: {c.gain:.1f}×, Noise gain: {c.noise_gain:.1f}×. "
        f"Noise penalty: +{c.noise_gain - c.gain:.1f}×."
    ),
))


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY C — BANDWIDTH & SLEW RATE LIMITATIONS (R14–R19)
# ═══════════════════════════════════════════════════════════════════════════

_r(RuleDefinition(
    rule_id="R14",
    category="Bandwidth Limitation",
    severity="critical",
    condition=lambda c: c.signal_freq_hz > c.closed_loop_bw_hz,
    fault_name="Signal Frequency Exceeds Closed-Loop Bandwidth",
    root_cause=(
        "The signal frequency is beyond the -3 dB closed-loop bandwidth. "
        "The op-amp response rolls off at -20 dB/decade beyond this point. "
        "Output will be severely attenuated and phase-shifted."
    ),
    corrective_actions=[
        "Reduce gain to increase closed-loop bandwidth: BW = GBW / noise_gain",
        "Use a higher-GBW op-amp for this application",
        "Lower the signal frequency if this is a test condition",
    ],
    engineering_detail=lambda c: (
        f"f_signal = {c.signal_freq_hz:.0f} Hz > f_CL = {c.closed_loop_bw_hz:.0f} Hz. "
        f"GBW = {c.gbw_hz / 1000:.0f} kHz. Attenuation ≈ "
        f"{-20 * __import__('math').log10(c.signal_freq_hz / c.closed_loop_bw_hz):.1f} dB."
    ),
))

_r(RuleDefinition(
    rule_id="R15",
    category="Bandwidth Limitation",
    severity="high",
    condition=lambda c: (
        0 < c.gbw_margin_hz < c.signal_freq_hz * 0.5
    ),
    fault_name="Insufficient GBW Margin — Less Than 50% Above Signal Frequency",
    root_cause=(
        "The closed-loop bandwidth is within 2× of the signal frequency. "
        "Phase shift within the pass-band will be significant (>26°), "
        "causing measurable frequency response error and group delay."
    ),
    corrective_actions=[
        "Design for closed-loop BW ≥ 10× signal frequency for flat response",
        "Reduce gain or select higher-GBW device",
    ],
    engineering_detail=lambda c: (
        f"GBW margin: {c.gbw_margin_hz:.0f} Hz. "
        f"Recommended minimum: {c.signal_freq_hz * 10:.0f} Hz (10× signal frequency)."
    ),
))

_r(RuleDefinition(
    rule_id="R16",
    category="Slew Rate",
    severity="critical",
    condition=lambda c: c.signal_freq_hz > c.slew_rate_limit_hz,
    fault_name="Slew Rate Limiting — Output Cannot Track Input",
    root_cause=(
        "The required dV/dt (2π × f × V_peak) exceeds the op-amp slew rate. "
        "The output slope is capped at SR, producing a triangular waveform instead of "
        "a sinusoid. This is a fundamental device speed limitation — no resistor or "
        "capacitor change can fix it; the device must be replaced."
    ),
    corrective_actions=[
        f"Maximum full-swing frequency for this circuit: f_max = SR / (2π × V_peak)",
        "Use a higher slew rate device: TL071 (13 V/µs), OPA2134 (20 V/µs)",
        "Reduce output swing (lower gain or input amplitude) to increase f_max",
        "Reduce signal frequency",
    ],
    engineering_detail=lambda c: (
        f"Required SR: 2π × {c.signal_freq_hz:.0f} Hz × {abs(c.expected_output_v):.2f} V = "
        f"{2 * 3.14159 * c.signal_freq_hz * abs(c.expected_output_v) / 1e6:.3f} V/µs. "
        f"Device SR: {c.slew_rate_vus:.1f} V/µs."
    ),
))

_r(RuleDefinition(
    rule_id="R17",
    category="Slew Rate",
    severity="high",
    condition=lambda c: (
        c.slew_rate_limit_hz > c.signal_freq_hz and
        c.slew_rate_limit_hz < c.signal_freq_hz * 2.0
    ),
    fault_name="Marginal Slew Rate — Less Than 2× Headroom",
    root_cause=(
        "The slew rate limit is within 2× of the signal frequency. "
        "Any increase in output amplitude, supply variation, or temperature increase "
        "will cause slew-rate limiting. Design standard requires ≥ 5–10× SR margin."
    ),
    corrective_actions=[
        "Aim for slew-rate headroom ≥ 5×: f_SR = SR / (2π × V_pk) ≥ 5 × f_signal",
        "Consider a higher slew rate op-amp",
    ],
    engineering_detail=lambda c: (
        f"f_SR_limit: {c.slew_rate_limit_hz:.0f} Hz, f_signal: {c.signal_freq_hz:.0f} Hz. "
        f"Margin ratio: {c.slew_rate_limit_hz / max(c.signal_freq_hz, 1):.1f}×."
    ),
))

_r(RuleDefinition(
    rule_id="R18",
    category="Bandwidth Limitation",
    severity="medium",
    condition=lambda c: c.closed_loop_bw_hz < 20_000 and c.circuit_type != "integrator",
    fault_name="Closed-Loop Bandwidth Below 20 kHz Audio Limit",
    root_cause=(
        "The -3 dB closed-loop bandwidth is below 20 kHz. This will roll off "
        "high-frequency audio content and is unacceptable for full-bandwidth "
        "audio amplifier applications."
    ),
    corrective_actions=[
        "Reduce gain to extend bandwidth above 20 kHz",
        "Select an op-amp with higher GBW: NE5532 (10 MHz), OPA2134 (8 MHz), TL071 (3 MHz)",
        f"Required GBW = noise_gain × 20 kHz = {20000 * 0:.0f} Hz minimum",
    ],
    engineering_detail=lambda c: (
        f"Closed-loop BW: {c.closed_loop_bw_hz:.0f} Hz. "
        f"Required GBW for 20 kHz at gain {c.noise_gain:.0f}: "
        f"{c.noise_gain * 20000 / 1000:.0f} kHz."
    ),
))

_r(RuleDefinition(
    rule_id="R19",
    category="Bandwidth Limitation",
    severity="low",
    condition=lambda c: (
        c.circuit_type == "integrator" and c.signal_freq_hz < 100
    ),
    fault_name="Integrator at Very Low Frequency — DC Saturation Risk",
    root_cause=(
        "An integrator circuit at very low frequencies (< 100 Hz) will accumulate "
        "charge from the op-amp's input offset voltage and bias current, driving the "
        "output to saturation over time. A reset or limiting mechanism is required."
    ),
    corrective_actions=[
        "Add a large feedback resistor in parallel with the integrating capacitor to limit DC gain",
        "Implement a reset switch across the capacitor",
        "Use an instrumentation-grade op-amp with very low Vos and Ib for integrators",
    ],
    engineering_detail=lambda c: (
        f"Integrator at {c.signal_freq_hz:.1f} Hz. "
        f"DC error: Vos × gain = {c.vos_output_error_mv:.2f} mV accumulated output offset."
    ),
))


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY D — OP-AMP STABILITY & OSCILLATION (R20–R25)
# ═══════════════════════════════════════════════════════════════════════════

_r(RuleDefinition(
    rule_id="R20",
    category="Stability",
    severity="critical",
    condition=lambda c: c.phase_margin_deg < 30.0,
    fault_name="Phase Margin Below 30° — Oscillation Risk",
    root_cause=(
        "Phase margin < 30° indicates the feedback loop is close to instability. "
        "With component tolerances, stray capacitance, or load changes, "
        "the circuit may break into sustained oscillation. "
        "The standard minimum is 45°; 60° is recommended for robust designs."
    ),
    corrective_actions=[
        "Increase noise gain to improve phase margin (phase margin improves with lower gain)",
        "Add a compensation capacitor (10–100 pF) in the feedback path",
        "Add a small series resistor (22–100 Ω) at the output to isolate capacitive loads",
        "Verify layout: minimize stray capacitance at inverting input",
    ],
    engineering_detail=lambda c: (
        f"Estimated phase margin: {c.phase_margin_deg:.1f}°. "
        f"Minimum recommended: 45°. Critical minimum: 30°."
    ),
))

_r(RuleDefinition(
    rule_id="R21",
    category="Stability",
    severity="high",
    condition=lambda c: 30.0 <= c.phase_margin_deg < 45.0,
    fault_name="Phase Margin 30–45° — Marginal Stability",
    root_cause=(
        "Phase margin is in the 30–45° range. The circuit is stable but with "
        "ringing on the step response. Component variations, PCB stray capacitance, "
        "or capacitive loading will push it toward the critical 30° boundary."
    ),
    corrective_actions=[
        "Add feedback compensation capacitor to improve phase margin to ≥ 60°",
        "Reduce gain or add a lead compensator in the feedback network",
        "Keep PCB traces at inverting input as short as possible (< 5 mm)",
    ],
    engineering_detail=lambda c: (
        f"Phase margin: {c.phase_margin_deg:.1f}°. Target: ≥ 60° for robust audio design."
    ),
))

_r(RuleDefinition(
    rule_id="R22",
    category="Stability",
    severity="high",
    condition=lambda c: (
        c.gain > 50 and c.gbw_hz < 3.0e6
    ),
    fault_name="High Gain with Low-GBW Device — Stability Risk",
    root_cause=(
        "Using a low-GBW op-amp (< 3 MHz) at high gain reduces closed-loop bandwidth "
        "into the range where parasitic poles in the feedback network may cause phase "
        "issues. LM358 and LM741 at gain > 50 are particularly susceptible."
    ),
    corrective_actions=[
        "Replace with TL071 (3 MHz GBW), NE5532 (10 MHz GBW), or OPA2134 (8 MHz GBW)",
        "Add phase compensation: 10–100 pF from output to inverting input",
        "Break the gain into two stages: each stage with lower individual gain",
    ],
    engineering_detail=lambda c: (
        f"Gain: {c.gain:.0f}×, GBW: {c.gbw_hz / 1e6:.1f} MHz. "
        f"Closed-loop BW: {c.closed_loop_bw_hz:.0f} Hz."
    ),
))

_r(RuleDefinition(
    rule_id="R23",
    category="Stability",
    severity="medium",
    condition=lambda c: (
        c.circuit_type == "comparator" and c.gain > 1
    ),
    fault_name="Op-Amp Used as Comparator — Potential Oscillation",
    root_cause=(
        "Using a general-purpose op-amp in open-loop comparator mode without "
        "hysteresis causes output chatter/oscillation at the threshold crossing "
        "due to noise. Dedicated comparators (LM393, LM339) have optimized "
        "output stages for this purpose."
    ),
    corrective_actions=[
        "Add hysteresis via positive feedback (Schmitt trigger configuration)",
        "Replace with dedicated comparator IC (LM393, TLV3201)",
        "Calculate required hysteresis: V_hys = (V_out_high − V_out_low) × R1/(R1+R2)",
    ],
    engineering_detail=lambda c: (
        f"Open-loop comparator mode. Without hysteresis, noise of "
        f">{c.input_referred_noise_nv:.0f} nV/√Hz at input causes output chatter."
    ),
))

_r(RuleDefinition(
    rule_id="R24",
    category="Stability",
    severity="medium",
    condition=lambda c: c.feedback_factor < 0.01,
    fault_name="Feedback Factor Too Small — Loop Gain Deficiency",
    root_cause=(
        "Feedback factor β = 1/noise_gain is very small (< 0.01), meaning loop gain "
        "T = Aol × β is insufficient to suppress distortion and output impedance effectively. "
        "The benefits of negative feedback (linearity, stability, bandwidth) are reduced."
    ),
    corrective_actions=[
        "Reduce closed-loop gain to improve feedback factor",
        "Use an op-amp with higher open-loop gain (Aol) to compensate",
        "Consider a topology change: current-feedback amplifier for very high gains",
    ],
    engineering_detail=lambda c: (
        f"β = {c.feedback_factor:.4f} (1/noise_gain = 1/{c.noise_gain:.1f}). "
        f"Loop gain T ≈ Aol × β (Aol ≈ 100 dB = 100,000): T ≈ {100000 * c.feedback_factor:.0f}."
    ),
))

_r(RuleDefinition(
    rule_id="R25",
    category="Stability",
    severity="info",
    condition=lambda c: c.circuit_type == "non_inverting" and c.gain == 1.0,
    fault_name="Unity Gain Stability Check",
    root_cause=(
        "Unity-gain configuration has the highest feedback factor (β = 1) and is "
        "the most demanding stability test for an op-amp. Some devices are not "
        "rated as unity-gain stable."
    ),
    corrective_actions=[
        "Verify device unity-gain stability in datasheet",
        "For unity-gain buffers, add 10–50 pF across feedback resistor if oscillation occurs",
    ],
    engineering_detail=lambda c: (
        f"β = 1.0 (maximum feedback). Most general-purpose op-amps are unity-gain stable."
    ),
))


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY E — NOISE SOURCES (R26–R30)
# ═══════════════════════════════════════════════════════════════════════════

_r(RuleDefinition(
    rule_id="R26",
    category="Noise",
    severity="high",
    condition=lambda c: c.output_noise_uv_rms > 1000.0,
    fault_name="Output Noise Exceeds 1 mVrms — Unacceptable for Audio",
    root_cause=(
        "Output-referred noise exceeds 1 mVrms. For reference, the noise floor of "
        "a 16-bit audio system is approximately 15 µVrms. 1 mVrms output noise will "
        "be clearly audible and degrade SNR to < 60 dB."
    ),
    corrective_actions=[
        "Use a low-noise op-amp: NE5532 (5 nV/√Hz), OPA2134 (8 nV/√Hz)",
        "Reduce bandwidth with a low-pass filter — noise ∝ √BW",
        "Reduce gain — output noise = en × noise_gain × √BW",
        "Shield input traces from EMI sources",
    ],
    engineering_detail=lambda c: (
        f"Output noise: {c.output_noise_uv_rms:.1f} µVrms "
        f"(en={c.input_referred_noise_nv:.0f} nV/√Hz × gain {c.noise_gain:.0f} × √BW)."
    ),
))

_r(RuleDefinition(
    rule_id="R27",
    category="Noise",
    severity="medium",
    condition=lambda c: c.input_referred_noise_nv > 30.0,
    fault_name="High Input Noise Density — Device Not Suited for Low-Noise Audio",
    root_cause=(
        "Input voltage noise density > 30 nV/√Hz makes this device unsuitable for "
        "low-noise audio applications (microphone preamps, phono stages, ADC buffers). "
        "LM358 (40 nV/√Hz) and LM741 (20 nV/√Hz) are notably noisy."
    ),
    corrective_actions=[
        "Replace with low-noise device: NE5532 (5 nV/√Hz), OPA2134 (8 nV/√Hz), AD797 (0.9 nV/√Hz)",
        "For microphone preamps, target en < 5 nV/√Hz",
        "Optimize source impedance to match device noise corner",
    ],
    engineering_detail=lambda c: (
        f"Device en: {c.input_referred_noise_nv:.0f} nV/√Hz. "
        f"Audio target: < 10 nV/√Hz for professional, < 20 nV/√Hz for consumer."
    ),
))

_r(RuleDefinition(
    rule_id="R28",
    category="Noise",
    severity="medium",
    condition=lambda c: c.thermal_noise_uv_rms > 5.0,
    fault_name="Thermal Noise Floor May Limit SNR",
    root_cause=(
        "Johnson-Nyquist thermal noise from the source resistance is significant. "
        "At 1 kΩ source impedance and 20 kHz bandwidth: Vn = √(4kTR×BW) ≈ 0.57 µVrms. "
        "At wider bandwidths or higher source resistance, this becomes a limiting factor."
    ),
    corrective_actions=[
        "Reduce source impedance to lower thermal noise floor",
        "Limit bandwidth to only what is required for the application",
        "Use a step-up transformer at the input to improve noise figure",
    ],
    engineering_detail=lambda c: (
        f"Thermal noise: {c.thermal_noise_uv_rms:.3f} µVrms "
        f"(4kTR×BW model, R=1kΩ, BW={c.closed_loop_bw_hz:.0f} Hz)."
    ),
))

_r(RuleDefinition(
    rule_id="R29",
    category="Noise",
    severity="high",
    condition=lambda c: (
        c.observed_issue in ["hum_noise", "hum", "50hz_noise", "60hz_noise", "ground_loop"]
    ),
    fault_name="Reported Hum / Ground Loop Noise",
    root_cause=(
        "User-reported hum/ground loop noise indicates power line coupling into the signal path. "
        "Root causes: inadequate power supply bypass capacitance, shared ground impedance "
        "between signal and power return paths, chassis ground loop creating circulating currents, "
        "or insufficient shielding on high-impedance input traces."
    ),
    corrective_actions=[
        "Add 100 nF ceramic + 10 µF electrolytic bypass capacitor at each supply pin, as close to the IC as possible",
        "Implement star grounding: signal ground, power ground, and chassis ground meet at a single point",
        "Route signal and power return traces separately; avoid shared ground impedance",
        "Shield high-impedance input nodes with a driven guard ring",
        "Add common-mode choke on power supply input leads",
        "For balanced systems: use differential input topology to reject common-mode hum",
    ],
    engineering_detail=lambda c: (
        f"Observed issue: '{c.observed_issue}'. CMRR: {__import__('app.engine.circuit_calculator', fromlist=['get_op_amp_specs']).get_op_amp_specs(c.op_amp_model)['cmrr_db']:.0f} dB."
    ),
))

_r(RuleDefinition(
    rule_id="R30",
    category="Noise",
    severity="medium",
    condition=lambda c: (
        c.observed_issue in ["hum_noise", "ground_loop"] and
        c.op_amp_model.upper() in ["LM358", "LM741"]
    ),
    fault_name="Low-CMRR Device with Ground Loop Noise",
    root_cause=(
        "LM358 and LM741 have relatively poor CMRR (65 dB and 70 dB respectively). "
        "When ground loop noise is present, the common-mode rejection is insufficient "
        "to adequately suppress it. A higher-CMRR device or differential topology is needed."
    ),
    corrective_actions=[
        "Replace with NE5532 (CMRR = 100 dB) or AD8221 instrumentation amplifier (CMRR = 130 dB)",
        "Use a fully differential amplifier topology",
        "Fix the ground loop root cause rather than compensating with a better device",
    ],
    engineering_detail=lambda c: (
        f"Device CMRR: ~65–70 dB. For hum rejection: target CMRR > 100 dB."
    ),
))


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY F — GROUND LOOP CONDITIONS (R31–R33)
# ═══════════════════════════════════════════════════════════════════════════

_r(RuleDefinition(
    rule_id="R31",
    category="Ground Loop",
    severity="high",
    condition=lambda c: c.observed_issue in ["ground_loop", "hum_noise", "hum"],
    fault_name="Ground Loop — Circulating Return Current Path",
    root_cause=(
        "A ground loop forms when two or more pieces of equipment have different "
        "ground potentials connected by signal cables. The potential difference drives "
        "a circulating current through the cable shield and signal return, inducing "
        "50/60 Hz (and harmonics) onto the signal. This is a system-level topology fault."
    ),
    corrective_actions=[
        "Break the loop: isolate one ground connection using an audio isolation transformer",
        "Use a ground loop isolator (Jensen JT-11P-1 or similar) on the affected signal path",
        "Route all equipment grounds to a single chassis star point",
        "Use balanced (XLR/TRS) interconnects with differential receivers",
        "Install ferrite chokes on signal cables to increase loop impedance",
    ],
    engineering_detail=lambda c: (
        "Ground loop: voltage between grounds V_gl = Z_ground × I_circulating. "
        "Even 10 mΩ of shared impedance with 1A power return = 10 mV hum."
    ),
))

_r(RuleDefinition(
    rule_id="R32",
    category="Ground Loop",
    severity="medium",
    condition=lambda c: (
        c.supply_voltage_v < 5.0 and
        c.observed_issue in ["ground_loop", "hum_noise", "hum", "noise"]
    ),
    fault_name="Low Supply Voltage Worsens Ground Loop Impact",
    root_cause=(
        "At low supply voltages, the signal dynamic range is compressed. "
        "A 50 mV ground loop error represents 10% of a ±5V swing but only 3% of ±15V. "
        "Low-supply systems have less dynamic range margin to tolerate ground loop interference."
    ),
    corrective_actions=[
        "Increase supply voltage to maximize signal-to-ground-loop-noise ratio",
        "Reduce ground loop amplitude by star grounding (primary fix)",
        "Implement a differential input stage to reject common-mode ground error",
    ],
    engineering_detail=lambda c: (
        f"Supply: {c.supply_voltage_v:.1f} V. "
        f"Ground loop impact: 50 mV hum = {50 / (c.supply_voltage_v * 10) * 100:.1f}% of dynamic range."
    ),
))

_r(RuleDefinition(
    rule_id="R33",
    category="Ground Loop",
    severity="info",
    condition=lambda c: c.op_amp_model.upper() == "AD8221",
    fault_name="Instrumentation Amplifier — Excellent Ground Loop Rejection",
    root_cause=(
        "The AD8221 instrumentation amplifier provides 130 dB CMRR, making it highly "
        "effective at rejecting ground loop noise. This is the correct device choice "
        "for high-noise environments."
    ),
    corrective_actions=[
        "Verify differential input wiring: IN+ and IN− carry signal, REF pin is output reference ground",
        "Set gain via single external resistor: G = 1 + (49.4 kΩ / R_G)",
        "Ensure REF pin is driven by a low-impedance source",
    ],
    engineering_detail=lambda c: (
        f"AD8221 CMRR: 130 dB. Ground loop of 100 mV → output error: "
        f"{100 * 10**(-130/20) * 1000:.4f} mV (negligible)."
    ),
))


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY G — THERMAL CONSTRAINTS (R34–R36)
# ═══════════════════════════════════════════════════════════════════════════

_r(RuleDefinition(
    rule_id="R34",
    category="Thermal",
    severity="medium",
    condition=lambda c: c.power_dissipation_mw > 500.0,
    fault_name="Estimated Power Dissipation May Require Thermal Management",
    root_cause=(
        "Static quiescent power dissipation estimate exceeds 500 mW. "
        "For TO-92 or SOT-23 packages with θJA ≈ 150–200 °C/W, "
        "500 mW could result in junction temperatures of 75–100 °C above ambient "
        "without a heat sink or adequate PCB copper area."
    ),
    corrective_actions=[
        "Calculate junction temperature: Tj = Ta + (Pd × θJA)",
        "Use DIP-8 or SOP-8 package with copper fill on PCB for heat spreading",
        "Add heat sink if Tj > 85°C under worst-case ambient",
        "Reduce supply voltage to the minimum required to lower quiescent dissipation",
    ],
    engineering_detail=lambda c: (
        f"Estimated Pd: {c.power_dissipation_mw:.0f} mW (Iq ≈ 1 mA × 2×Vs={2*c.supply_voltage_v:.0f} V)."
    ),
))

_r(RuleDefinition(
    rule_id="R35",
    category="Thermal",
    severity="medium",
    condition=lambda c: (
        c.supply_voltage_v > 12.0 and c.gain > 20
    ),
    fault_name="High Supply + High Gain — Elevated Output Stage Dissipation",
    root_cause=(
        "At high supply voltages, the output stage transistors must dissipate "
        "(Vsupply − Vout) × Iout as heat. With high gain, the output approaches the "
        "supply rail, reducing dissipation. But in the linear region, dissipation peaks "
        "at Vout = Vsupply / 2."
    ),
    corrective_actions=[
        "Verify thermal rating of output stage at worst-case linear region operation",
        "Use split supply to reduce individual rail-to-output voltage drop",
        "Add output current limiting if driving low-impedance loads",
    ],
    engineering_detail=lambda c: (
        f"Max output stage Pd (linear region): ({c.supply_voltage_v:.0f} V)² / (8 × R_load). "
        f"Ensure R_load > {c.supply_voltage_v**2 / (8 * 0.5):.0f} Ω for 500 mW limit."
    ),
))

_r(RuleDefinition(
    rule_id="R36",
    category="Thermal",
    severity="low",
    condition=lambda c: (
        c.op_amp_model.upper() == "LM741" and c.supply_voltage_v > 15.0
    ),
    fault_name="LM741 at High Supply — Thermal Stress Risk",
    root_cause=(
        "The LM741 has a relatively high quiescent current (1.7 mA typical) and "
        "was designed for ±15V operation as its nominal supply. At ±18–22V, "
        "quiescent dissipation (1.7 mA × 36V = 61 mW) may cause thermal stress "
        "in elevated ambient temperatures."
    ),
    corrective_actions=[
        "Operate LM741 at ±15V nominal (30V total) where quiescent Pd ≈ 51 mW",
        "Consider modern low-power replacement: OPA2134 (Iq = 4 mA but lower noise)",
        "Verify operating temperature range does not exceed 70°C (commercial grade)",
    ],
    engineering_detail=lambda c: (
        f"LM741 at {c.supply_voltage_v:.0f}V. Iq=1.7mA → Pd={1.7*c.supply_voltage_v*2:.0f} mW."
    ),
))


# ═══════════════════════════════════════════════════════════════════════════
# CATEGORY H — SIGNAL INTEGRITY & MISCELLANEOUS (R37–R38)
# ═══════════════════════════════════════════════════════════════════════════

_r(RuleDefinition(
    rule_id="R37",
    category="Signal Integrity",
    severity="medium",
    condition=lambda c: (
        c.observed_issue in ["distortion", "no_output", "gain_instability"] and
        c.circuit_type == "non_inverting"
    ),
    fault_name="Non-Inverting: Input Common-Mode Range Violation Risk",
    root_cause=(
        "Non-inverting amplifiers apply the input signal directly to the op-amp's "
        "non-inverting input. If the input signal approaches the supply rail (or the "
        "common-mode input range limit), input stage distortion occurs. "
        "LM358 CM range extends to V- but not to V+."
    ),
    corrective_actions=[
        "Verify signal stays within device common-mode input range (datasheet, Table of Electrical Characteristics)",
        "Add a resistor divider to bias the input to mid-supply for single-supply designs",
        "For rail-to-rail input requirement, select rail-to-rail input op-amp",
    ],
    engineering_detail=lambda c: (
        f"Non-inverting input = Vin = {c.input_signal_mv:.1f} mV. "
        f"Supply: ±{c.supply_voltage_v:.1f} V. "
        f"Verify CM range in datasheet for {c.op_amp_model}."
    ),
))

_r(RuleDefinition(
    rule_id="R38",
    category="Signal Integrity",
    severity="high",
    condition=lambda c: (
        c.observed_issue in ["no_output", "low_output"] and
        abs(c.expected_output_v) > 0.1
    ),
    fault_name="No Output Reported Despite Valid Expected Output — Wiring or Bias Fault",
    root_cause=(
        "Calculation shows a valid expected output voltage but user reports no output. "
        "Root causes: open feedback resistor, incorrect pin wiring, missing supply bypass "
        "capacitors causing oscillation/latch-up, output short to ground, or device latch-up "
        "due to input exceeding supply during power-up."
    ),
    corrective_actions=[
        "Verify all power supply pins are connected and measure voltage at IC supply pins",
        "Check continuity of feedback resistor with DMM in-circuit",
        "Verify non-inverting input is not floating (floating input = unpredictable output)",
        "Check for latch-up: power cycle with input signal present and monitor quiescent current",
        "Measure output with no load to rule out output short circuit",
        "Add 100 nF bypass capacitor directly at each supply pin if not present",
    ],
    engineering_detail=lambda c: (
        f"Expected output: {c.expected_output_v:.3f} V. "
        f"Reported: no output. Supply: ±{c.supply_voltage_v:.1f} V. "
        f"Check power, wiring, and bypass capacitors first."
    ),
))


# ---------------------------------------------------------------------------
# Convenience accessor
# ---------------------------------------------------------------------------
def get_rule_by_id(rule_id: str) -> RuleDefinition | None:
    """Return the rule with the given ID, or None."""
    return next((r for r in RULES if r.rule_id == rule_id), None)


def get_rules_by_category(category: str) -> list[RuleDefinition]:
    """Return all rules matching the given category string (case-insensitive)."""
    cat = category.lower()
    return [r for r in RULES if r.category.lower() == cat]
