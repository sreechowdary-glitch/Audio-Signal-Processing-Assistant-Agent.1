"""
fft_analyzer.py
---------------
Frequency-domain analysis: FFT spectrum, dominant frequencies, harmonic series.

Responsibilities
----------------
- Apply Hann window and compute one-sided rfft magnitude spectrum.
- Identify dominant frequencies (top-N peaks by magnitude).
- Extract harmonic series from the fundamental frequency.
- Render a log-scale FFT spectrum plot to Base64 PNG.
- Return FFTResult dataclass consumed by fault_detector, API routes, PDF generator.

Dependencies: numpy, scipy, matplotlib
"""

import base64
import io
import logging
from dataclasses import dataclass, field

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import find_peaks

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FIG_WIDTH   = 10.0
_FIG_HEIGHT  = 3.5
_DPI         = 120
_GRID_ALPHA  = 0.25
_SPEC_COLOR  = "#16a34a"
_PEAK_COLOR  = "#e11d48"

_NUM_DOMINANT   = 5      # how many dominant frequencies to report
_HARMONIC_COUNT = 6      # how many harmonics to look for
_HARMONIC_TOL   = 0.05   # ±5 % tolerance when matching harmonics
_MIN_PEAK_DB    = -80.0  # floor for peak detection in dB


# ---------------------------------------------------------------------------
# Typed results
# ---------------------------------------------------------------------------
@dataclass
class FrequencyPeak:
    freq_hz:      float
    magnitude_db: float
    bin_index:    int


@dataclass
class HarmonicComponent:
    harmonic_number: int          # 1 = fundamental, 2 = 2nd, …
    freq_hz:         float
    magnitude_db:    float
    present:         bool         # True if found above noise floor


@dataclass
class FFTResult:
    """
    Attributes
    ----------
    freqs           : frequency axis (Hz), length = N//2 + 1
    magnitude_db    : one-sided magnitude spectrum in dBFS
    dominant_freqs  : top-N FrequencyPeak objects sorted by magnitude
    fundamental_hz  : estimated fundamental frequency (Hz)
    harmonics       : list of HarmonicComponent (up to _HARMONIC_COUNT)
    freq_resolution : FFT bin width in Hz
    plot_b64        : Base64 PNG of the spectrum plot
    """
    freqs:           np.ndarray
    magnitude_db:    np.ndarray
    dominant_freqs:  list[FrequencyPeak]
    fundamental_hz:  float
    harmonics:       list[HarmonicComponent]
    freq_resolution: float
    plot_b64:        str = field(repr=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def analyse_fft(signal: np.ndarray, sample_rate: int) -> FFTResult:
    """
    Compute the FFT magnitude spectrum of a mono float32 signal.

    Parameters
    ----------
    signal      : mono float32 numpy array
    sample_rate : sample rate in Hz

    Returns
    -------
    FFTResult
    """
    logger.info("FFT analysis: %d samples, sr=%d Hz", len(signal), sample_rate)

    N = len(signal)

    # -- 1. Windowing --------------------------------------------------------
    window = np.hanning(N)
    windowed = signal * window

    # -- 2. rfft (one-sided) -------------------------------------------------
    spectrum = np.fft.rfft(windowed, n=N)
    freqs    = np.fft.rfftfreq(N, d=1.0 / sample_rate)

    # -- 3. Magnitude in dBFS -----------------------------------------------
    magnitude   = np.abs(spectrum) * (2.0 / N)   # amplitude-correct for one-sided
    magnitude[0]  /= 2.0                           # DC bin needs no doubling
    # Guard against log(0)
    magnitude_clipped = np.maximum(magnitude, 1e-10)
    magnitude_db = 20.0 * np.log10(magnitude_clipped)

    freq_resolution = float(sample_rate) / N

    # -- 4. Dominant frequency peaks ----------------------------------------
    dominant = _find_dominant_peaks(freqs, magnitude_db)

    # -- 5. Fundamental & harmonics -----------------------------------------
    fundamental_hz = dominant[0].freq_hz if dominant else 0.0
    harmonics = _extract_harmonics(freqs, magnitude_db, fundamental_hz)

    # -- 6. Plot -------------------------------------------------------------
    plot_b64 = _render_fft_plot(freqs, magnitude_db, dominant, sample_rate)

    logger.info(
        "Fundamental: %.1f Hz | Dominant peaks: %s",
        fundamental_hz,
        [f"{p.freq_hz:.1f} Hz" for p in dominant[:3]],
    )

    return FFTResult(
        freqs=freqs,
        magnitude_db=magnitude_db,
        dominant_freqs=dominant,
        fundamental_hz=round(fundamental_hz, 2),
        harmonics=harmonics,
        freq_resolution=round(freq_resolution, 4),
        plot_b64=plot_b64,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _find_dominant_peaks(
    freqs: np.ndarray,
    magnitude_db: np.ndarray,
) -> list[FrequencyPeak]:
    """Return the top-N frequency peaks above _MIN_PEAK_DB."""
    peak_indices, props = find_peaks(
        magnitude_db,
        height=_MIN_PEAK_DB,
        prominence=3.0,
        distance=5,
    )

    if len(peak_indices) == 0:
        return []

    # Sort by magnitude descending, keep top _NUM_DOMINANT
    sorted_idx = np.argsort(magnitude_db[peak_indices])[::-1][:_NUM_DOMINANT]
    selected   = peak_indices[sorted_idx]

    return [
        FrequencyPeak(
            freq_hz=round(float(freqs[i]), 2),
            magnitude_db=round(float(magnitude_db[i]), 2),
            bin_index=int(i),
        )
        for i in selected
    ]


def _extract_harmonics(
    freqs: np.ndarray,
    magnitude_db: np.ndarray,
    fundamental_hz: float,
) -> list[HarmonicComponent]:
    """
    Given a fundamental frequency, look for the first _HARMONIC_COUNT harmonics.
    A harmonic is considered 'present' if its expected frequency has a local
    magnitude within ±_HARMONIC_TOL of the expected value AND above -60 dBFS.
    """
    if fundamental_hz < 20.0:   # below audible range — skip
        return []

    harmonics: list[HarmonicComponent] = []
    freq_step = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0

    for n in range(1, _HARMONIC_COUNT + 1):
        expected_hz = fundamental_hz * n
        if expected_hz > freqs[-1]:
            break

        # Find the bin closest to expected_hz
        tol_hz  = expected_hz * _HARMONIC_TOL
        lo_bin  = int(np.searchsorted(freqs, expected_hz - tol_hz))
        hi_bin  = int(np.searchsorted(freqs, expected_hz + tol_hz)) + 1
        hi_bin  = min(hi_bin, len(freqs) - 1)

        if lo_bin >= hi_bin:
            harmonics.append(HarmonicComponent(n, round(expected_hz, 2), -120.0, False))
            continue

        best_bin = lo_bin + int(np.argmax(magnitude_db[lo_bin:hi_bin]))
        mag      = float(magnitude_db[best_bin])
        present  = mag > -60.0

        harmonics.append(HarmonicComponent(
            harmonic_number=n,
            freq_hz=round(float(freqs[best_bin]), 2),
            magnitude_db=round(mag, 2),
            present=present,
        ))

    return harmonics


def _render_fft_plot(
    freqs:        np.ndarray,
    magnitude_db: np.ndarray,
    dominant:     list[FrequencyPeak],
    sample_rate:  int,
) -> str:
    """Render log-scale FFT spectrum to Base64 PNG."""
    # Clip display range to 20 Hz – Nyquist
    nyquist = sample_rate // 2
    mask = (freqs >= 20) & (freqs <= nyquist)
    f_plot = freqs[mask]
    m_plot = magnitude_db[mask]

    fig, ax = plt.subplots(figsize=(_FIG_WIDTH, _FIG_HEIGHT), dpi=_DPI)
    ax.semilogx(f_plot, m_plot, color=_SPEC_COLOR, linewidth=0.8, alpha=0.9)
    ax.fill_between(f_plot, _MIN_PEAK_DB, m_plot,
                    color=_SPEC_COLOR, alpha=0.12)

    # Mark dominant peaks
    for pk in dominant[:_NUM_DOMINANT]:
        if 20.0 <= pk.freq_hz <= nyquist:
            ax.axvline(pk.freq_hz, color=_PEAK_COLOR, linewidth=0.9,
                       linestyle="--", alpha=0.7)
            ax.annotate(
                f"{pk.freq_hz:.0f} Hz",
                xy=(pk.freq_hz, pk.magnitude_db),
                xytext=(4, 4),
                textcoords="offset points",
                fontsize=7,
                color=_PEAK_COLOR,
            )

    ax.set_xlabel("Frequency (Hz) — log scale", fontsize=9)
    ax.set_ylabel("Magnitude (dBFS)", fontsize=9)
    ax.set_title("FFT Frequency Spectrum", fontsize=10, fontweight="bold")
    ax.set_xlim(20, nyquist)
    ax.set_ylim(max(m_plot.min() - 5, -100), 5)
    ax.grid(True, which="both", alpha=_GRID_ALPHA)
    ax.tick_params(labelsize=8)

    # X-axis octave labels
    for f_label in [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]:
        if f_label <= nyquist:
            ax.axvline(f_label, color="#e5e7eb", linewidth=0.5, zorder=0)

    fig.tight_layout()
    b64 = _fig_to_b64(fig)
    plt.close(fig)
    return b64


def _fig_to_b64(fig: plt.Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")