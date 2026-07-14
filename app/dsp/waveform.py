"""
waveform.py
-----------
Time-domain waveform analysis and peak detection.

Responsibilities
----------------
- Build time-axis array matched to the signal length.
- Detect peak sample locations using SciPy find_peaks with configurable
  prominence and distance thresholds.
- Render a publication-quality waveform plot to a Base64 PNG string.
- Return a WaveformResult dataclass consumed by the API route and PDF generator.

Dependencies: numpy, scipy, matplotlib, dataclasses
"""

import base64
import io
import logging
from dataclasses import dataclass, field

import matplotlib
matplotlib.use("Agg")          # non-interactive backend — must be set before pyplot import
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import find_peaks

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plot styling constants
# ---------------------------------------------------------------------------
_FIG_WIDTH      = 10.0
_FIG_HEIGHT     = 3.2
_WAVEFORM_COLOR = "#3b82d4"
_PEAK_COLOR     = "#e11d48"
_GRID_ALPHA     = 0.25
_DPI            = 120

# Peak detection defaults
_PEAK_PROMINENCE    = 0.15   # minimum fractional amplitude prominence
_PEAK_MIN_DISTANCE  = 100    # minimum sample distance between peaks


# ---------------------------------------------------------------------------
# Typed result
# ---------------------------------------------------------------------------
@dataclass
class WaveformResult:
    """
    Attributes
    ----------
    time_axis       : 1-D float array of time values in seconds
    amplitude       : 1-D float32 signal samples
    peak_indices    : sample indices of detected peaks
    peak_times      : corresponding time values for detected peaks
    peak_amplitudes : signal values at detected peak indices
    num_peaks       : total peaks detected
    plot_b64        : Base64-encoded PNG of the waveform plot (UTF-8 str)
    """
    time_axis:       np.ndarray
    amplitude:       np.ndarray
    peak_indices:    np.ndarray
    peak_times:      np.ndarray
    peak_amplitudes: np.ndarray
    num_peaks:       int
    plot_b64:        str = field(repr=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def analyse_waveform(signal: np.ndarray, sample_rate: int) -> WaveformResult:
    """
    Perform time-domain analysis on a mono float32 signal.

    Parameters
    ----------
    signal      : mono float32 numpy array normalised to [-1, 1]
    sample_rate : sampling frequency in Hz

    Returns
    -------
    WaveformResult
    """
    logger.info("Waveform analysis: %d samples at %d Hz", len(signal), sample_rate)

    # -- 1. Time axis --------------------------------------------------------
    time_axis = np.linspace(0.0, len(signal) / sample_rate, num=len(signal), endpoint=False)

    # -- 2. Peak detection ---------------------------------------------------
    # Work on absolute value to catch both positive and negative peaks
    abs_signal = np.abs(signal)
    min_height = float(np.max(abs_signal)) * 0.4   # only prominent peaks
    peak_indices, _ = find_peaks(
        abs_signal,
        height=min_height,
        prominence=_PEAK_PROMINENCE,
        distance=_PEAK_MIN_DISTANCE,
    )

    # Limit to top-20 peaks by amplitude to keep the plot readable
    if len(peak_indices) > 20:
        top20 = np.argsort(abs_signal[peak_indices])[-20:]
        peak_indices = peak_indices[top20]

    peak_times      = time_axis[peak_indices]
    peak_amplitudes = signal[peak_indices]

    logger.info("Detected %d peaks", len(peak_indices))

    # -- 3. Plot -------------------------------------------------------------
    plot_b64 = _render_waveform_plot(signal, time_axis, peak_indices, sample_rate)

    return WaveformResult(
        time_axis=time_axis,
        amplitude=signal,
        peak_indices=peak_indices,
        peak_times=peak_times,
        peak_amplitudes=peak_amplitudes,
        num_peaks=int(len(peak_indices)),
        plot_b64=plot_b64,
    )


# ---------------------------------------------------------------------------
# Internal: plot renderer
# ---------------------------------------------------------------------------
def _render_waveform_plot(
    signal:      np.ndarray,
    time_axis:   np.ndarray,
    peak_indices: np.ndarray,
    sample_rate: int,
) -> str:
    """Render waveform + peaks to a Base64 PNG string."""

    # Downsample for plotting if signal is very long (> 200k samples)
    MAX_PLOT_SAMPLES = 200_000
    if len(signal) > MAX_PLOT_SAMPLES:
        step = len(signal) // MAX_PLOT_SAMPLES
        plot_time = time_axis[::step]
        plot_sig  = signal[::step]
        # Rescale peak indices to downsampled space for marker placement
        plot_peak_idx = peak_indices // step
    else:
        plot_time    = time_axis
        plot_sig     = signal
        plot_peak_idx = peak_indices

    fig, ax = plt.subplots(figsize=(_FIG_WIDTH, _FIG_HEIGHT), dpi=_DPI)

    ax.plot(plot_time, plot_sig, color=_WAVEFORM_COLOR, linewidth=0.6, alpha=0.9,
            label="Waveform")

    if len(plot_peak_idx) > 0:
        ax.scatter(
            plot_time[plot_peak_idx],
            plot_sig[plot_peak_idx],
            color=_PEAK_COLOR,
            s=22,
            zorder=5,
            label=f"Peaks ({len(peak_indices)})",
        )

    # Clipping indicator lines
    ax.axhline( 0.99, color="#e11d48", linewidth=0.8, linestyle="--", alpha=0.6, label="Clip threshold")
    ax.axhline(-0.99, color="#e11d48", linewidth=0.8, linestyle="--", alpha=0.6)

    ax.set_xlabel("Time (s)", fontsize=9)
    ax.set_ylabel("Amplitude", fontsize=9)
    ax.set_title("Time-Domain Waveform", fontsize=10, fontweight="bold")
    ax.set_xlim(plot_time[0], plot_time[-1])
    ax.set_ylim(-1.1, 1.1)
    ax.grid(True, alpha=_GRID_ALPHA)
    ax.legend(fontsize=8, loc="upper right")
    ax.tick_params(labelsize=8)

    fig.tight_layout()
    b64 = _fig_to_b64(fig)
    plt.close(fig)
    return b64


def _fig_to_b64(fig: plt.Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")
