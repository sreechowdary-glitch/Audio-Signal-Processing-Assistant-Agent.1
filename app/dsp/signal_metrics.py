"""
signal_metrics.py
-----------------
Engineering signal quality metrics: RMS, Peak Amplitude, Crest Factor, SNR.

Responsibilities
----------------
- Compute RMS level in dBFS (decibels relative to full scale).
- Compute peak amplitude (normalised 0–1).
- Compute crest factor = peak / RMS in dB.
- Estimate SNR using a noise floor estimate from the quietest signal frame.
- Return a SignalMetrics dataclass with all numeric results.

No plotting in this module — it is a pure numeric calculation engine.

Dependencies: numpy, scipy
"""

import logging
from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, lfilter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SNR estimation parameters
# ---------------------------------------------------------------------------
_FRAME_SIZE    = 2048    # samples per frame for noise floor estimation
_NOISE_PERCENTILE = 10   # use the bottom 10th-percentile frame energy as noise floor


# ---------------------------------------------------------------------------
# Typed result
# ---------------------------------------------------------------------------
@dataclass
class SignalMetrics:
    """
    Attributes
    ----------
    rms_linear      : RMS amplitude (linear, 0–1 scale)
    rms_dbfs        : RMS in dBFS  (negative value; 0 dBFS = full scale)
    peak_amplitude  : maximum |sample| value (0–1 normalised)
    peak_dbfs       : peak in dBFS
    crest_factor_db : peak_dbfs − rms_dbfs  (dynamic headroom indicator)
    snr_db          : estimated Signal-to-Noise Ratio in dB
    dc_offset       : mean sample value (ideal = 0.0)
    dynamic_range_db: difference between loudest and quietest non-silent frame
    """
    rms_linear:       float
    rms_dbfs:         float
    peak_amplitude:   float
    peak_dbfs:        float
    crest_factor_db:  float
    snr_db:           float
    dc_offset:        float
    dynamic_range_db: float


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compute_metrics(signal: np.ndarray, sample_rate: int) -> SignalMetrics:
    """
    Compute all signal quality metrics for a mono float32 signal.

    Parameters
    ----------
    signal      : mono float32 numpy array (values should be in [-1, 1])
    sample_rate : sample rate in Hz (used for frame-based SNR estimation)

    Returns
    -------
    SignalMetrics
    """
    logger.info("Computing signal metrics for %d samples", len(signal))

    # -- 1. DC offset --------------------------------------------------------
    dc_offset = float(np.mean(signal))

    # -- 2. RMS --------------------------------------------------------------
    rms_linear = float(np.sqrt(np.mean(signal ** 2)))
    rms_dbfs   = _safe_db(rms_linear)

    # -- 3. Peak amplitude ---------------------------------------------------
    peak_amplitude = float(np.max(np.abs(signal)))
    peak_dbfs      = _safe_db(peak_amplitude)

    # -- 4. Crest factor -----------------------------------------------------
    if rms_linear > 0:
        crest_factor_db = peak_dbfs - rms_dbfs
    else:
        crest_factor_db = 0.0

    # -- 5. SNR estimation ---------------------------------------------------
    snr_db = _estimate_snr(signal)

    # -- 6. Dynamic range (frame-based) -------------------------------------
    dynamic_range_db = _compute_dynamic_range(signal)

    metrics = SignalMetrics(
        rms_linear=round(rms_linear, 6),
        rms_dbfs=round(rms_dbfs, 2),
        peak_amplitude=round(peak_amplitude, 6),
        peak_dbfs=round(peak_dbfs, 2),
        crest_factor_db=round(crest_factor_db, 2),
        snr_db=round(snr_db, 2),
        dc_offset=round(dc_offset, 6),
        dynamic_range_db=round(dynamic_range_db, 2),
    )

    logger.info(
        "Metrics — RMS: %.2f dBFS | Peak: %.2f dBFS | SNR: %.2f dB | CF: %.2f dB",
        metrics.rms_dbfs, metrics.peak_dbfs, metrics.snr_db, metrics.crest_factor_db,
    )

    return metrics


# ---------------------------------------------------------------------------
# RMS helper
# ---------------------------------------------------------------------------
def compute_rms(signal: np.ndarray) -> float:
    """Return RMS amplitude (linear) of the signal. Convenience function."""
    return float(np.sqrt(np.mean(signal ** 2)))


def compute_rms_dbfs(signal: np.ndarray) -> float:
    """Return RMS in dBFS."""
    return _safe_db(compute_rms(signal))


# ---------------------------------------------------------------------------
# SNR estimation
# ---------------------------------------------------------------------------
def _estimate_snr(signal: np.ndarray) -> float:
    """
    SNR estimation via frame energy analysis.

    Method
    ------
    1. Split signal into non-overlapping frames of _FRAME_SIZE samples.
    2. Compute RMS energy per frame.
    3. Classify the bottom _NOISE_PERCENTILE % frames as noise.
    4. SNR = 20 * log10(RMS_signal / RMS_noise_floor)

    This approach is robust for real-world signals without a separate
    noise-only reference track.
    """
    if len(signal) < _FRAME_SIZE * 2:
        # Signal too short for frame-based estimation — use full-signal heuristic
        peak = float(np.max(np.abs(signal)))
        noise_floor = float(np.std(signal - np.mean(signal)))
        if noise_floor < 1e-10:
            return 96.0   # theoretical max for 16-bit
        return round(float(20.0 * np.log10(peak / noise_floor)), 2)

    # Trim to integer number of frames
    n_frames = len(signal) // _FRAME_SIZE
    frames   = signal[:n_frames * _FRAME_SIZE].reshape(n_frames, _FRAME_SIZE)

    frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))

    # Remove silent frames (RMS < -80 dBFS) before percentile calculation
    active_mask = frame_rms > 10 ** (-80 / 20)
    active_rms  = frame_rms[active_mask]

    if len(active_rms) < 2:
        return 0.0

    # Signal = 90th percentile frame, Noise = 10th percentile frame
    signal_level = float(np.percentile(active_rms, 90))
    noise_level  = float(np.percentile(active_rms, _NOISE_PERCENTILE))

    if noise_level < 1e-10:
        return 96.0   # essentially noiseless

    snr = 20.0 * np.log10(signal_level / noise_level)
    return float(np.clip(snr, -20.0, 96.0))


# ---------------------------------------------------------------------------
# Dynamic range
# ---------------------------------------------------------------------------
def _compute_dynamic_range(signal: np.ndarray) -> float:
    """
    Estimate dynamic range as the spread of frame-level RMS values (in dB).
    Frames below -80 dBFS are excluded as silence.
    """
    if len(signal) < _FRAME_SIZE * 2:
        return 0.0

    n_frames = len(signal) // _FRAME_SIZE
    frames   = signal[:n_frames * _FRAME_SIZE].reshape(n_frames, _FRAME_SIZE)
    frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))

    active = frame_rms[frame_rms > 10 ** (-80 / 20)]
    if len(active) < 2:
        return 0.0

    loud  = float(np.percentile(active, 95))
    quiet = float(np.percentile(active, 5))

    if quiet < 1e-10:
        return 96.0

    return float(np.clip(20.0 * np.log10(loud / quiet), 0.0, 96.0))


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def _safe_db(linear: float) -> float:
    """Convert linear amplitude to dBFS, guarding against log(0)."""
    if linear < 1e-10:
        return -120.0
    return float(20.0 * np.log10(linear))
