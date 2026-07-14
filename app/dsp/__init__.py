"""
app/dsp/__init__.py
-------------------
Public interface for the DSP Analysis Engine package.

Exports the key classes and the single top-level orchestrator function
`run_full_analysis()` that the Flask API route calls.

Import from here — not from individual sub-modules — to keep route code clean.
"""

from app.dsp.audio_loader import (
    AudioSignal,
    AudioLoadError,
    UnsupportedFormatError,
    FileTooLargeError,
    load_audio,
    load_audio_from_bytes,
)
from app.dsp.waveform import WaveformResult, analyse_waveform
from app.dsp.fft_analyzer import FFTResult, FrequencyPeak, HarmonicComponent, analyse_fft
from app.dsp.spectrogram import SpectrogramResult, analyse_spectrogram
from app.dsp.signal_metrics import SignalMetrics, compute_metrics
from app.dsp.fault_detector import FaultResult, detect_all_faults

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Composite result returned by run_full_analysis()
# ---------------------------------------------------------------------------
@dataclass
class FullAnalysisResult:
    """
    Aggregates all DSP engine outputs into a single object.

    Passed to:
    - Flask route  → serialised to JSON response
    - IBM Granite  → used to construct the explanation prompt
    - PDF generator → used to render the report
    - SQLite ORM   → key fields persisted to audio_analysis table
    """
    audio:       AudioSignal
    waveform:    WaveformResult
    fft:         FFTResult
    spectrogram: SpectrogramResult
    metrics:     SignalMetrics
    faults:      list[FaultResult]


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------
def run_full_analysis(file_path: str | Path) -> FullAnalysisResult:
    """
    Load an audio file and run the complete DSP analysis pipeline.

    Pipeline order
    --------------
    1. Load & decode audio (audio_loader)
    2. Time-domain waveform analysis (waveform)
    3. FFT frequency analysis (fft_analyzer)
    4. Spectrogram generation (spectrogram)
    5. Signal quality metrics (signal_metrics)
    6. Fault detection — clipping, hum × 2, THD (fault_detector)

    Parameters
    ----------
    file_path : path to the saved audio file on disk

    Returns
    -------
    FullAnalysisResult
    """
    logger.info("=== Starting full DSP analysis for: %s ===", file_path)

    audio     = load_audio(file_path)
    waveform  = analyse_waveform(audio.signal, audio.sample_rate)
    fft       = analyse_fft(audio.signal, audio.sample_rate)
    spectro   = analyse_spectrogram(audio.signal, audio.sample_rate)
    metrics   = compute_metrics(audio.signal, audio.sample_rate)
    faults    = detect_all_faults(audio.signal, audio.sample_rate, fft_result=fft)

    detected = [f.fault for f in faults if f.detected]
    logger.info(
        "=== Analysis complete — detected faults: %s ===",
        detected if detected else "None",
    )

    return FullAnalysisResult(
        audio=audio,
        waveform=waveform,
        fft=fft,
        spectrogram=spectro,
        metrics=metrics,
        faults=faults,
    )


def run_full_analysis_from_bytes(file_bytes: bytes, filename: str) -> FullAnalysisResult:
    """
    Run full DSP analysis from in-memory bytes (no filesystem write required).

    Useful for streaming uploads or test scenarios.
    """
    logger.info("=== Starting full DSP analysis from bytes: %s ===", filename)

    audio     = load_audio_from_bytes(file_bytes, filename)
    waveform  = analyse_waveform(audio.signal, audio.sample_rate)
    fft       = analyse_fft(audio.signal, audio.sample_rate)
    spectro   = analyse_spectrogram(audio.signal, audio.sample_rate)
    metrics   = compute_metrics(audio.signal, audio.sample_rate)
    faults    = detect_all_faults(audio.signal, audio.sample_rate, fft_result=fft)

    return FullAnalysisResult(
        audio=audio,
        waveform=waveform,
        fft=fft,
        spectrogram=spectro,
        metrics=metrics,
        faults=faults,
    )


__all__ = [
    # Loader
    "AudioSignal", "AudioLoadError", "UnsupportedFormatError",
    "FileTooLargeError", "load_audio", "load_audio_from_bytes",
    # Analysis results
    "WaveformResult", "FFTResult", "FrequencyPeak", "HarmonicComponent",
    "SpectrogramResult", "SignalMetrics", "FaultResult",
    # Orchestrator
    "FullAnalysisResult", "run_full_analysis", "run_full_analysis_from_bytes",
]
