"""
fault_detector.py
-----------------
Audio fault detection: Clipping, Hum Noise (50/60 Hz), and Distortion (THD).

Responsibilities
----------------
- Clipping Detector  : identify hard and soft clipping from sample statistics.
- Hum Detector       : detect 50 Hz (EU power line) and 60 Hz (US power line) hum
                       via narrow-band energy analysis on the FFT spectrum.
- Distortion Detector: compute Total Harmonic Distortion (THD) from harmonic
                       magnitudes in the FFT result.
- Each detector returns a FaultResult with: fault name, confidence (0.0–1.0),
  severity label, and a plain-English engineering explanation.

Dependencies: numpy, scipy, fft_analyzer (internal), signal_metrics (internal)

Design rule
-----------
This module consumes pre-computed FFTResult data where possible.
If no FFTResult is supplied, it computes a minimal internal FFT.
No AI calls. No external I/O.
"""

import logging
from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, sosfilt

from app.dsp.fft_analyzer import FFTResult, analyse_fft

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds — tuned for practical audio engineering use
# ---------------------------------------------------------------------------

# --- Clipping ---
_CLIP_HARD_THRESHOLD    = 0.999   # normalised amplitude; above = hard clip
_CLIP_SOFT_THRESHOLD    = 0.95    # above = soft/near-clip warning
_CLIP_HARD_MIN_RATIO    = 0.001   # ≥ 0.1 % of samples above threshold = detected
_CLIP_SOFT_MIN_RATIO    = 0.005   # ≥ 0.5 % soft-clip samples for warning

# --- Hum ---
_HUM_FREQS              = {50: "50 Hz — EU Power Line", 60: "60 Hz — US Power Line"}
_HUM_BW_HZ              = 3.0     # ±3 Hz band around hum frequency
_HUM_HARMONIC_CHECKS    = [1, 2, 3]  # check fundamental + 2nd + 3rd harmonic
_HUM_CONFIDENCE_THRESH  = -50.0   # dBFS; hum bin must exceed this floor
_HUM_RELATIVE_THRESH_DB = -40.0   # hum bin must be within 40 dB of signal peak

# --- Distortion / THD ---
_THD_MIN_FUNDAMENTAL_DB = -60.0   # fundamental must be above this to attempt THD
_THD_HARMONICS          = 5       # number of harmonics to include in THD
_THD_DISTORTION_THRESH  = 0.01    # 1 % THD = notable; 3 % = audible; 10 % = severe


# ---------------------------------------------------------------------------
# Typed result
# ---------------------------------------------------------------------------
@dataclass
class FaultResult:
    """
    Attributes
    ----------
    fault       : fault name string  e.g. 'Clipping', 'Hum Noise (50 Hz)', 'Distortion'
    detected    : True if fault is confirmed above threshold
    confidence  : float 0.0–1.0
    severity    : 'None' | 'Low' | 'Medium' | 'High' | 'Critical'
    explanation : plain-English engineering explanation of the detected condition
    detail      : optional dict with numeric supporting data
    """
    fault:       str
    detected:    bool
    confidence:  float
    severity:    str
    explanation: str
    detail:      dict


# ---------------------------------------------------------------------------
# Public API — top-level entry point
# ---------------------------------------------------------------------------
def detect_all_faults(
    signal:      np.ndarray,
    sample_rate: int,
    fft_result:  FFTResult | None = None,
) -> list[FaultResult]:
    """
    Run all fault detectors and return a list of FaultResult objects.

    Parameters
    ----------
    signal      : mono float32 numpy array
    sample_rate : Hz
    fft_result  : optional pre-computed FFTResult; computed internally if None

    Returns
    -------
    list[FaultResult] — one entry per detector, regardless of detection outcome
    """
    if fft_result is None:
        logger.info("No FFTResult supplied — computing internal FFT for fault detection")
        fft_result = analyse_fft(signal, sample_rate)

    results: list[FaultResult] = []

    results.append(detect_clipping(signal))
    results.append(detect_hum(fft_result, sample_rate, target_hz=50))
    results.append(detect_hum(fft_result, sample_rate, target_hz=60))
    results.append(detect_distortion(fft_result))

    detected_count = sum(1 for r in results if r.detected)
    logger.info("Fault detection complete: %d/%d faults detected", detected_count, len(results))

    return results


# ---------------------------------------------------------------------------
# Detector 1 — Clipping
# ---------------------------------------------------------------------------
def detect_clipping(signal: np.ndarray) -> FaultResult:
    """
    Detect hard and soft clipping by counting samples at or near ±1.0.

    Hard clipping : samples >= _CLIP_HARD_THRESHOLD (0.999)
    Soft clipping : samples >= _CLIP_SOFT_THRESHOLD (0.95) (but not hard clip)

    Returns
    -------
    FaultResult with fault = 'Clipping'
    """
    abs_sig = np.abs(signal)
    n_total = len(signal)

    hard_mask = abs_sig >= _CLIP_HARD_THRESHOLD
    soft_mask = (abs_sig >= _CLIP_SOFT_THRESHOLD) & ~hard_mask

    hard_count = int(np.sum(hard_mask))
    soft_count = int(np.sum(soft_mask))

    hard_ratio = hard_count / n_total
    soft_ratio = soft_count / n_total

    # Confidence: scaled by ratio of clipped samples, capped at 1.0
    if hard_ratio >= _CLIP_HARD_MIN_RATIO:
        detected    = True
        confidence  = float(np.clip(hard_ratio / 0.05, 0.0, 1.0))  # 5 % = full confidence
        severity    = _clip_severity(hard_ratio)
        explanation = (
            f"Hard clipping detected: {hard_count:,} samples ({hard_ratio*100:.2f}%) "
            f"are at or above {_CLIP_HARD_THRESHOLD:.3f} (full-scale saturation). "
            f"This indicates the amplifier output is driven beyond the supply rail, "
            f"causing waveform flat-topping, severe harmonic distortion, and potential "
            f"speaker/load damage. Root cause: gain too high for the input signal level "
            f"or supply voltage insufficient."
        )
    elif soft_ratio >= _CLIP_SOFT_MIN_RATIO:
        detected    = True
        confidence  = float(np.clip(soft_ratio / 0.02, 0.0, 0.75))
        severity    = "Low" if soft_ratio < 0.02 else "Medium"
        explanation = (
            f"Soft clipping / near-saturation detected: {soft_count:,} samples "
            f"({soft_ratio*100:.2f}%) exceed {_CLIP_SOFT_THRESHOLD:.2f} normalised "
            f"amplitude. The signal is approaching full scale — risk of hard clipping "
            f"with any gain increase. Recommend reducing input signal level by "
            f"{_headroom_needed(abs_sig):.1f} dB."
        )
    else:
        detected    = False
        confidence  = 0.0
        severity    = "None"
        explanation = (
            f"No clipping detected. Maximum sample amplitude: "
            f"{float(np.max(abs_sig)):.4f} ({_safe_db(float(np.max(abs_sig))):.1f} dBFS). "
            f"Signal headroom is adequate."
        )

    return FaultResult(
        fault="Clipping",
        detected=detected,
        confidence=round(confidence, 3),
        severity=severity,
        explanation=explanation,
        detail={
            "hard_clip_count": hard_count,
            "soft_clip_count": soft_count,
            "hard_clip_ratio": round(hard_ratio, 6),
            "soft_clip_ratio": round(soft_ratio, 6),
            "peak_amplitude":  round(float(np.max(abs_sig)), 6),
            "peak_dbfs":       round(_safe_db(float(np.max(abs_sig))), 2),
        },
    )


# ---------------------------------------------------------------------------
# Detector 2 — Hum Noise (50 Hz or 60 Hz)
# ---------------------------------------------------------------------------
def detect_hum(
    fft_result:  FFTResult,
    sample_rate: int,
    target_hz:   int = 50,
) -> FaultResult:
    """
    Detect power-line hum at target_hz (50 or 60 Hz) and its harmonics.

    Strategy
    --------
    1. Extract FFT magnitude in a ±_HUM_BW_HZ band around each harmonic.
    2. Compare maximum magnitude in hum band against:
       a) Absolute floor (_HUM_CONFIDENCE_THRESH dBFS)
       b) Relative floor (signal_peak − 40 dB)
    3. Confidence score = weighted sum across checked harmonics.

    Parameters
    ----------
    fft_result : pre-computed FFTResult
    sample_rate: Hz
    target_hz  : 50 or 60
    """
    freqs        = fft_result.freqs
    magnitude_db = fft_result.magnitude_db
    signal_peak_db = float(np.max(magnitude_db))
    relative_floor = signal_peak_db + _HUM_RELATIVE_THRESH_DB

    hum_evidence: list[dict] = []
    weighted_confidence = 0.0
    weight_sum = 0.0

    for n in _HUM_HARMONIC_CHECKS:
        freq_target = target_hz * n
        if freq_target > freqs[-1]:
            break

        lo = freq_target - _HUM_BW_HZ
        hi = freq_target + _HUM_BW_HZ
        band_mask = (freqs >= lo) & (freqs <= hi)

        if not np.any(band_mask):
            continue

        band_mag     = magnitude_db[band_mask]
        peak_bin_mag = float(np.max(band_mag))
        peak_bin_hz  = float(freqs[band_mask][np.argmax(band_mag)])

        above_abs      = peak_bin_mag > _HUM_CONFIDENCE_THRESH
        above_relative = peak_bin_mag > relative_floor

        # Weight: fundamental gets weight 1.0, 2nd harmonic 0.6, 3rd 0.3
        weight = 1.0 / n
        if above_abs and above_relative:
            # Scale confidence by how far above the floor we are
            margin = peak_bin_mag - max(_HUM_CONFIDENCE_THRESH, relative_floor)
            contrib = float(np.clip(margin / 20.0, 0.0, 1.0)) * weight
        else:
            contrib = 0.0

        weighted_confidence += contrib
        weight_sum += weight

        hum_evidence.append({
            "harmonic": n,
            "expected_hz": freq_target,
            "detected_hz": round(peak_bin_hz, 2),
            "magnitude_db": round(peak_bin_mag, 2),
            "above_floor": above_abs,
        })

    # Normalise confidence to [0, 1]
    confidence = float(np.clip(weighted_confidence / max(weight_sum, 1e-6), 0.0, 1.0))
    detected   = confidence > 0.25   # threshold for positive detection

    label = _HUM_FREQS.get(target_hz, f"{target_hz} Hz")

    if detected:
        fund_evidence = next((e for e in hum_evidence if e["harmonic"] == 1), None)
        fund_mag = fund_evidence["magnitude_db"] if fund_evidence else "N/A"
        severity = "High" if confidence > 0.7 else "Medium" if confidence > 0.4 else "Low"
        explanation = (
            f"Power-line hum detected at {label}. "
            f"Fundamental at {target_hz} Hz: {fund_mag} dBFS. "
            f"This is characteristic of inadequate power supply decoupling, "
            f"ground loop coupling, or insufficient bypass capacitance on the supply rails. "
            f"Recommended fixes: add 100 nF ceramic + 10 µF electrolytic bypass caps per "
            f"supply pin, verify ground plane continuity, and check for ground loop paths "
            f"between chassis, signal, and power grounds."
        )
    else:
        severity    = "None"
        explanation = (
            f"No significant {label} hum detected. "
            f"Power supply coupling appears adequate at this frequency."
        )

    return FaultResult(
        fault=f"Hum Noise ({target_hz} Hz)",
        detected=detected,
        confidence=round(confidence, 3),
        severity=severity,
        explanation=explanation,
        detail={
            "target_hz":     target_hz,
            "label":         label,
            "harmonics_checked": hum_evidence,
        },
    )


# ---------------------------------------------------------------------------
# Detector 3 — Distortion (THD)
# ---------------------------------------------------------------------------
def detect_distortion(fft_result: FFTResult) -> FaultResult:
    """
    Compute Total Harmonic Distortion (THD) from the FFT harmonic series.

    THD formula
    -----------
    THD = sqrt(V2² + V3² + V4² + V5²) / V1

    where V1 = fundamental amplitude (linear), V2…V5 = harmonic amplitudes.

    A harmonic is only included if it is marked `present` in the FFTResult.
    """
    harmonics = fft_result.harmonics
    if not harmonics:
        return FaultResult(
            fault="Distortion (THD)",
            detected=False,
            confidence=0.0,
            severity="None",
            explanation="Insufficient harmonic data to compute THD (no harmonics extracted).",
            detail={"thd_percent": None},
        )

    # Fundamental
    fundamental = next((h for h in harmonics if h.harmonic_number == 1), None)
    if fundamental is None or not fundamental.present:
        return FaultResult(
            fault="Distortion (THD)",
            detected=False,
            confidence=0.0,
            severity="None",
            explanation=(
                "Fundamental frequency not identified — THD calculation not possible. "
                "Signal may be broadband noise or below minimum level threshold."
            ),
            detail={"thd_percent": None},
        )

    v1_db = fundamental.magnitude_db
    if v1_db < _THD_MIN_FUNDAMENTAL_DB:
        return FaultResult(
            fault="Distortion (THD)",
            detected=False,
            confidence=0.0,
            severity="None",
            explanation=(
                f"Fundamental ({fundamental.freq_hz} Hz) magnitude too low "
                f"({v1_db:.1f} dBFS) for reliable THD measurement."
            ),
            detail={"thd_percent": None},
        )

    v1_linear = _db_to_linear(v1_db)

    # Harmonic amplitudes (V2 … V_N)
    harmonic_powers = []
    harmonic_detail = []
    for h in harmonics:
        if h.harmonic_number == 1:
            continue
        if h.harmonic_number > _THD_HARMONICS + 1:
            break
        if h.present:
            v_n = _db_to_linear(h.magnitude_db)
            harmonic_powers.append(v_n ** 2)
        else:
            harmonic_powers.append(0.0)

        harmonic_detail.append({
            "harmonic": h.harmonic_number,
            "freq_hz":  h.freq_hz,
            "mag_db":   h.magnitude_db,
            "present":  h.present,
        })

    if not harmonic_powers or sum(harmonic_powers) == 0.0:
        thd_percent = 0.0
    else:
        thd_linear  = float(np.sqrt(sum(harmonic_powers))) / v1_linear
        thd_percent = round(thd_linear * 100.0, 3)

    # Severity and confidence
    if thd_percent >= 10.0:
        detected    = True
        severity    = "Critical"
        confidence  = 1.0
    elif thd_percent >= 3.0:
        detected    = True
        severity    = "High"
        confidence  = 0.9
    elif thd_percent >= 1.0:
        detected    = True
        severity    = "Medium"
        confidence  = 0.7
    elif thd_percent >= 0.1:
        detected    = True
        severity    = "Low"
        confidence  = 0.5
    else:
        detected    = False
        severity    = "None"
        confidence  = 0.0

    if detected:
        explanation = (
            f"Total Harmonic Distortion (THD) = {thd_percent:.2f}%. "
            f"Fundamental: {fundamental.freq_hz:.1f} Hz at {v1_db:.1f} dBFS. "
            + _thd_interpretation(thd_percent)
        )
    else:
        explanation = (
            f"THD = {thd_percent:.3f}% — within acceptable range for audio applications. "
            f"Harmonic content is at normal levels for a linear amplifier."
        )

    return FaultResult(
        fault="Distortion (THD)",
        detected=detected,
        confidence=round(confidence, 3),
        severity=severity,
        explanation=explanation,
        detail={
            "thd_percent":      thd_percent,
            "fundamental_hz":   fundamental.freq_hz,
            "fundamental_db":   v1_db,
            "harmonics":        harmonic_detail,
        },
    )


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------
def _clip_severity(ratio: float) -> str:
    if ratio >= 0.10:
        return "Critical"
    if ratio >= 0.03:
        return "High"
    if ratio >= 0.005:
        return "Medium"
    return "Low"


def _headroom_needed(abs_sig: np.ndarray) -> float:
    """Return dB of attenuation needed to bring peak to -1 dBFS."""
    peak = float(np.max(abs_sig))
    target = 10 ** (-1 / 20)   # -1 dBFS
    if peak <= target:
        return 0.0
    return 20.0 * np.log10(peak / target)


def _thd_interpretation(thd_percent: float) -> str:
    if thd_percent >= 10.0:
        return (
            "Severe distortion: signal integrity is significantly compromised. "
            "Likely causes: op-amp operating outside linear range, output stage "
            "clipping, or incorrect biasing. Immediate circuit review required."
        )
    if thd_percent >= 3.0:
        return (
            "Audible and measurable distortion. Probable causes: gain set too high, "
            "insufficient slew rate for signal frequency, or thermal distortion in "
            "output stage. Reduce gain or improve supply decoupling."
        )
    if thd_percent >= 1.0:
        return (
            "Moderate distortion — perceptible in high-fidelity applications. "
            "Review op-amp gain-bandwidth product against signal frequency. "
            "Ensure feedback network impedance is correct."
        )
    return (
        "Low-level distortion — borderline for precision applications. "
        "Monitor with increasing signal level."
    )


def _safe_db(linear: float) -> float:
    if linear < 1e-10:
        return -120.0
    return float(20.0 * np.log10(linear))


def _db_to_linear(db: float) -> float:
    return float(10.0 ** (db / 20.0))
