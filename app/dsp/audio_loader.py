"""
audio_loader.py
---------------
WAV / MP3 file decoder and validator.

Responsibilities
----------------
- Validate file extension and magic bytes (not just extension).
- Decode audio to mono float32 NumPy array via Librosa.
- Return a typed AudioSignal dataclass consumed by all downstream DSP modules.
- Raise typed exceptions so Flask routes can return precise HTTP error codes.

Dependencies: librosa, soundfile, numpy, pathlib, dataclasses
"""

import io
import logging
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Magic-byte signatures for allowed audio containers
# ---------------------------------------------------------------------------
_MAGIC_BYTES: dict[str, bytes] = {
    "wav":  b"RIFF",          # RIFF....WAVE
    "mp3":  b"\xff\xfb",      # MPEG-1 Layer 3 sync word (most common)
    "mp3i": b"ID3",           # ID3-tagged MP3
    "flac": b"fLaC",
}

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"wav", "mp3", "flac"})
_MAX_DURATION_SEC: float = 300.0   # 5 minutes upper-bound
_TARGET_SR: int = 22_050            # Librosa default; explicit here for clarity


# ---------------------------------------------------------------------------
# Typed result
# ---------------------------------------------------------------------------
@dataclass
class AudioSignal:
    """
    Immutable container returned by load_audio().

    Attributes
    ----------
    signal      : float32 NumPy array, mono, normalised to [-1.0, 1.0]
    sample_rate : original sample rate of the file (Hz)
    duration_sec: duration in seconds
    num_channels: original channel count before mono downmix
    file_format : detected container format ('wav' | 'mp3' | 'flac')
    filename    : sanitised filename passed in
    """
    signal:       np.ndarray
    sample_rate:  int
    duration_sec: float
    num_channels: int
    file_format:  str
    filename:     str


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------
class AudioLoadError(Exception):
    """Raised for any error during file loading or validation."""


class UnsupportedFormatError(AudioLoadError):
    """Raised when the file format is not in the allowed set."""


class FileTooLargeError(AudioLoadError):
    """Raised when the audio duration exceeds _MAX_DURATION_SEC."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_audio(file_path: str | Path) -> AudioSignal:
    """
    Load and validate an audio file, returning an AudioSignal.

    Parameters
    ----------
    file_path : str or Path
        Path to the audio file on the server filesystem.

    Returns
    -------
    AudioSignal

    Raises
    ------
    UnsupportedFormatError  – extension or magic bytes not recognised
    FileTooLargeError       – audio exceeds maximum allowed duration
    AudioLoadError          – any other decoding failure
    """
    path = Path(file_path)
    logger.info("Loading audio file: %s", path.name)

    # -- 1. Extension check --------------------------------------------------
    ext = path.suffix.lstrip(".").lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Extension '.{ext}' is not supported. Allowed: {_ALLOWED_EXTENSIONS}"
        )

    # -- 2. Magic-byte validation --------------------------------------------
    _validate_magic_bytes(path, ext)

    # -- 3. Probe channel count before mono downmix --------------------------
    try:
        probe = librosa.load(path, sr=None, mono=False, duration=0.1)
        raw_channels = 1 if probe[0].ndim == 1 else probe[0].shape[0]
    except Exception:
        raw_channels = 1  # best-effort; non-fatal

    # -- 4. Decode to mono float32 at native sample rate ---------------------
    try:
        signal, sr = librosa.load(path, sr=None, mono=True, dtype=np.float32)
    except Exception as exc:
        raise AudioLoadError(f"Librosa failed to decode '{path.name}': {exc}") from exc

    # -- 5. Duration guard ---------------------------------------------------
    duration = float(len(signal)) / sr
    if duration > _MAX_DURATION_SEC:
        raise FileTooLargeError(
            f"Audio duration {duration:.1f}s exceeds the {_MAX_DURATION_SEC}s limit."
        )

    logger.info(
        "Loaded '%s': sr=%d Hz, duration=%.2fs, channels=%d",
        path.name, sr, duration, raw_channels,
    )

    return AudioSignal(
        signal=signal,
        sample_rate=int(sr),
        duration_sec=round(duration, 4),
        num_channels=raw_channels,
        file_format=ext,
        filename=path.name,
    )


def load_audio_from_bytes(file_bytes: bytes, filename: str) -> AudioSignal:
    """
    Load audio directly from an in-memory bytes buffer.
    Useful when the Flask route has the bytes before writing to disk.

    Parameters
    ----------
    file_bytes : bytes  – raw file content
    filename   : str    – original filename (used for extension detection)
    """
    ext = Path(filename).suffix.lstrip(".").lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Extension '.{ext}' is not supported. Allowed: {_ALLOWED_EXTENSIONS}"
        )

    _validate_magic_bytes_buffer(file_bytes, ext)

    try:
        buf = io.BytesIO(file_bytes)
        signal, sr = librosa.load(buf, sr=None, mono=True, dtype=np.float32)
    except Exception as exc:
        raise AudioLoadError(f"Failed to decode '{filename}': {exc}") from exc

    duration = float(len(signal)) / sr
    if duration > _MAX_DURATION_SEC:
        raise FileTooLargeError(
            f"Audio duration {duration:.1f}s exceeds the {_MAX_DURATION_SEC}s limit."
        )

    return AudioSignal(
        signal=signal,
        sample_rate=int(sr),
        duration_sec=round(duration, 4),
        num_channels=1,
        file_format=ext,
        filename=filename,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _validate_magic_bytes(path: Path, ext: str) -> None:
    """Read the first 12 bytes and confirm the container signature."""
    with open(path, "rb") as fh:
        header = fh.read(12)
    _check_header(header, ext, str(path.name))


def _validate_magic_bytes_buffer(buf: bytes, ext: str) -> None:
    _check_header(buf[:12], ext, "<buffer>")


def _check_header(header: bytes, ext: str, name: str) -> None:
    if ext == "wav":
        if not header.startswith(_MAGIC_BYTES["wav"]):
            raise UnsupportedFormatError(
                f"'{name}' does not appear to be a valid WAV file (RIFF header missing)."
            )
    elif ext == "mp3":
        if not (header.startswith(_MAGIC_BYTES["mp3"]) or
                header.startswith(_MAGIC_BYTES["mp3i"])):
            raise UnsupportedFormatError(
                f"'{name}' does not appear to be a valid MP3 file."
            )
    elif ext == "flac":
        if not header.startswith(_MAGIC_BYTES["flac"]):
            raise UnsupportedFormatError(
                f"'{name}' does not appear to be a valid FLAC file."
            )
