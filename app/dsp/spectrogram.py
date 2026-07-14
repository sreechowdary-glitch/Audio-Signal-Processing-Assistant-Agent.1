"""
spectrogram.py
--------------
Short-Time Fourier Transform (STFT) spectrogram generation.

Responsibilities
----------------
- Compute STFT with configurable hop length and window size.
- Convert to dBFS power spectrogram.
- Render a time-frequency heatmap plot to Base64 PNG.
- Optionally compute a Mel-scale spectrogram for perceptual analysis.
- Return SpectrogramResult dataclass with both representations.

Dependencies: librosa, numpy, matplotlib
"""

import base64
import io
import logging
from dataclasses import dataclass, field

import librosa
import librosa.display
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# STFT Parameters
# ---------------------------------------------------------------------------
_N_FFT      = 2048     # FFT window size in samples
_HOP_LENGTH = 512      # hop between frames (75% overlap with 2048 window)
_N_MELS     = 128      # Mel filterbank bins
_FMIN       = 20.0     # lowest frequency for Mel scale (Hz)
_REF_DB     = 1.0      # reference for dBFS conversion

_FIG_WIDTH  = 10.0
_FIG_HEIGHT = 3.8
_DPI        = 120


# ---------------------------------------------------------------------------
# Typed result
# ---------------------------------------------------------------------------
@dataclass
class SpectrogramResult:
    """
    Attributes
    ----------
    stft_db          : 2-D array [freq_bins × time_frames], STFT power in dBFS
    mel_db           : 2-D array [n_mels × time_frames], Mel-scale power in dBFS
    times            : time axis for STFT frames (seconds)
    freqs            : frequency axis for STFT bins (Hz)
    n_fft            : FFT window size used
    hop_length       : hop length used
    time_resolution  : seconds per frame
    freq_resolution  : Hz per bin
    stft_plot_b64    : Base64 PNG of STFT spectrogram
    mel_plot_b64     : Base64 PNG of Mel spectrogram
    """
    stft_db:         np.ndarray
    mel_db:          np.ndarray
    times:           np.ndarray
    freqs:           np.ndarray
    n_fft:           int
    hop_length:      int
    time_resolution: float
    freq_resolution: float
    stft_plot_b64:   str = field(repr=False)
    mel_plot_b64:    str = field(repr=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def analyse_spectrogram(signal: np.ndarray, sample_rate: int) -> SpectrogramResult:
    """
    Compute STFT and Mel spectrograms for the given mono signal.

    Parameters
    ----------
    signal      : mono float32 numpy array
    sample_rate : sample rate in Hz

    Returns
    -------
    SpectrogramResult
    """
    logger.info("Spectrogram analysis: %d samples at %d Hz", len(signal), sample_rate)

    # -- 1. STFT magnitude to power dB ---------------------------------------
    stft_complex = librosa.stft(signal, n_fft=_N_FFT, hop_length=_HOP_LENGTH)
    stft_magnitude = np.abs(stft_complex)
    stft_db = librosa.amplitude_to_db(stft_magnitude, ref=_REF_DB)

    # -- 2. Frequency and time axes ------------------------------------------
    freqs = librosa.fft_frequencies(sr=sample_rate, n_fft=_N_FFT)
    times = librosa.frames_to_time(
        np.arange(stft_db.shape[1]),
        sr=sample_rate,
        hop_length=_HOP_LENGTH,
        n_fft=_N_FFT,
    )

    time_resolution = float(_HOP_LENGTH) / sample_rate
    freq_resolution = float(sample_rate) / _N_FFT

    # -- 3. Mel spectrogram --------------------------------------------------
    mel_spec = librosa.feature.melspectrogram(
        y=signal,
        sr=sample_rate,
        n_fft=_N_FFT,
        hop_length=_HOP_LENGTH,
        n_mels=_N_MELS,
        fmin=_FMIN,
    )
    mel_db = librosa.power_to_db(mel_spec, ref=np.max)

    # -- 4. Render plots -----------------------------------------------------
    stft_plot_b64 = _render_stft_plot(stft_db, times, freqs, sample_rate)
    mel_plot_b64  = _render_mel_plot(mel_db, times, sample_rate)

    logger.info(
        "Spectrogram: %d freq bins × %d frames | %.3f s/frame | %.2f Hz/bin",
        stft_db.shape[0], stft_db.shape[1], time_resolution, freq_resolution,
    )

    return SpectrogramResult(
        stft_db=stft_db,
        mel_db=mel_db,
        times=times,
        freqs=freqs,
        n_fft=_N_FFT,
        hop_length=_HOP_LENGTH,
        time_resolution=round(time_resolution, 6),
        freq_resolution=round(freq_resolution, 4),
        stft_plot_b64=stft_plot_b64,
        mel_plot_b64=mel_plot_b64,
    )


# ---------------------------------------------------------------------------
# Internal: plot renderers
# ---------------------------------------------------------------------------
def _render_stft_plot(
    stft_db:     np.ndarray,
    times:       np.ndarray,
    freqs:       np.ndarray,
    sample_rate: int,
) -> str:
    """Render STFT power spectrogram as a 2-D heatmap (linear frequency axis)."""
    fig, ax = plt.subplots(figsize=(_FIG_WIDTH, _FIG_HEIGHT), dpi=_DPI)

    img = librosa.display.specshow(
        stft_db,
        sr=sample_rate,
        hop_length=_HOP_LENGTH,
        x_axis="time",
        y_axis="linear",
        cmap="magma",
        ax=ax,
    )

    cbar = fig.colorbar(img, ax=ax, format="%+2.0f dB", pad=0.01)
    cbar.ax.tick_params(labelsize=7)

    ax.set_title("STFT Spectrogram (Linear Frequency)", fontsize=10, fontweight="bold")
    ax.set_xlabel("Time (s)", fontsize=9)
    ax.set_ylabel("Frequency (Hz)", fontsize=9)
    ax.tick_params(labelsize=8)

    fig.tight_layout()
    b64 = _fig_to_b64(fig)
    plt.close(fig)
    return b64


def _render_mel_plot(
    mel_db:      np.ndarray,
    times:       np.ndarray,
    sample_rate: int,
) -> str:
    """Render Mel-scale spectrogram heatmap."""
    fig, ax = plt.subplots(figsize=(_FIG_WIDTH, _FIG_HEIGHT), dpi=_DPI)

    img = librosa.display.specshow(
        mel_db,
        sr=sample_rate,
        hop_length=_HOP_LENGTH,
        x_axis="time",
        y_axis="mel",
        fmin=_FMIN,
        cmap="inferno",
        ax=ax,
    )

    cbar = fig.colorbar(img, ax=ax, format="%+2.0f dB", pad=0.01)
    cbar.ax.tick_params(labelsize=7)

    ax.set_title("Mel Spectrogram (Perceptual Scale)", fontsize=10, fontweight="bold")
    ax.set_xlabel("Time (s)", fontsize=9)
    ax.set_ylabel("Frequency (Mel)", fontsize=9)
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
