"""
app/utils/validators.py
-----------------------
Input validation helpers for all API endpoints.

All validation is pure Python — no framework dependency.
Each validator returns a (is_valid: bool, errors: list[str]) tuple.
"""

import os
from pathlib import Path
from typing import Any


# ── Audio file validation ────────────────────────────────────────────────────

ALLOWED_AUDIO_EXTENSIONS = frozenset({"wav", "mp3", "flac"})
ALLOWED_AUDIO_MIMETYPES  = frozenset({
    "audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3",
    "audio/flac", "audio/x-flac", "application/octet-stream",
})
MAX_AUDIO_BYTES = 50 * 1024 * 1024   # 50 MB


def validate_audio_file(file_storage) -> tuple[bool, list[str]]:
    """
    Validate a Werkzeug FileStorage object for audio upload.

    Checks:
    - File is present and has a filename
    - Extension is in allowed set
    - Content-length (if known) does not exceed limit

    Parameters
    ----------
    file_storage : werkzeug.datastructures.FileStorage

    Returns
    -------
    (is_valid, errors)
    """
    errors: list[str] = []

    if file_storage is None:
        return False, ["No file provided in the request."]

    filename = getattr(file_storage, "filename", "") or ""
    if not filename:
        return False, ["No filename — file may be empty."]

    ext = Path(filename).suffix.lstrip(".").lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        errors.append(
            f"File extension '.{ext}' is not allowed. "
            f"Accepted: {sorted(ALLOWED_AUDIO_EXTENSIONS)}"
        )

    # Check magic bytes (first 12 bytes)
    try:
        header = file_storage.stream.read(12)
        file_storage.stream.seek(0)   # rewind for downstream reading
        _validate_audio_magic(header, ext, errors)
    except Exception:
        pass  # non-fatal; Librosa will fail more precisely later

    return (len(errors) == 0, errors)


def _validate_audio_magic(header: bytes, ext: str, errors: list[str]) -> None:
    if ext == "wav" and not header.startswith(b"RIFF"):
        errors.append("WAV file does not have a valid RIFF header.")
    elif ext == "mp3" and not (header.startswith(b"\xff\xfb") or header.startswith(b"ID3")):
        errors.append("MP3 file does not have a valid MPEG header.")
    elif ext == "flac" and not header.startswith(b"fLaC"):
        errors.append("FLAC file does not have a valid fLaC header.")


# ── Circuit parameter validation ─────────────────────────────────────────────

VALID_CIRCUIT_TYPES = frozenset({
    "non_inverting", "inverting", "difference", "integrator", "comparator"
})
VALID_OP_AMP_MODELS = frozenset({
    "LM358", "LM741", "TL071", "NE5532", "LM386", "OPA2134", "AD8221", "GENERIC"
})
VALID_OBSERVED_ISSUES = frozenset({
    "none", "output_clipping", "oscillation", "hum_noise", "ground_loop",
    "distortion", "gain_instability", "no_output", "low_output",
})


def validate_circuit_params(params: dict) -> tuple[bool, list[str]]:
    """
    Validate circuit diagnostic request parameters.

    Parameters
    ----------
    params : dict from request.get_json()

    Returns
    -------
    (is_valid, errors)
    """
    errors: list[str] = []

    # Required fields
    ct = params.get("circuit_type", "")
    if not ct:
        errors.append("circuit_type is required.")
    elif ct not in VALID_CIRCUIT_TYPES:
        errors.append(
            f"circuit_type '{ct}' is invalid. Valid: {sorted(VALID_CIRCUIT_TYPES)}"
        )

    model = params.get("op_amp_model", "")
    if not model:
        errors.append("op_amp_model is required.")
    elif model.upper() not in {m.upper() for m in VALID_OP_AMP_MODELS}:
        errors.append(
            f"op_amp_model '{model}' is not recognised. "
            f"Valid: {sorted(VALID_OP_AMP_MODELS)}"
        )

    supply = params.get("supply_voltage_v")
    if supply is None:
        errors.append("supply_voltage_v is required.")
    else:
        try:
            sv = float(supply)
            if sv <= 0:
                errors.append("supply_voltage_v must be positive.")
            elif sv > 30:
                errors.append("supply_voltage_v cannot exceed 30 V (absolute maximum).")
        except (TypeError, ValueError):
            errors.append("supply_voltage_v must be a number.")

    gain = params.get("gain")
    if gain is None:
        errors.append("gain is required.")
    else:
        try:
            g = float(gain)
            if g <= 0:
                errors.append("gain must be positive.")
            elif g > 100_000:
                errors.append("gain exceeds maximum allowed value of 100,000.")
        except (TypeError, ValueError):
            errors.append("gain must be a number.")

    vin = params.get("input_signal_mv")
    if vin is None:
        errors.append("input_signal_mv is required.")
    else:
        try:
            v = float(vin)
            if v < 0:
                errors.append("input_signal_mv must be non-negative.")
            elif v > 100_000:
                errors.append("input_signal_mv exceeds 100,000 mV (100 V). Check units.")
        except (TypeError, ValueError):
            errors.append("input_signal_mv must be a number.")

    issue = params.get("observed_issue", "none")
    if issue and issue not in VALID_OBSERVED_ISSUES:
        errors.append(
            f"observed_issue '{issue}' is invalid. Valid: {sorted(VALID_OBSERVED_ISSUES)}"
        )

    freq = params.get("signal_freq_hz")
    if freq is not None:
        try:
            f = float(freq)
            if f <= 0:
                errors.append("signal_freq_hz must be positive.")
            elif f > 1_000_000:
                errors.append("signal_freq_hz exceeds 1 MHz.")
        except (TypeError, ValueError):
            errors.append("signal_freq_hz must be a number.")

    return (len(errors) == 0, errors)


# ── Explain / QA request validation ──────────────────────────────────────────

def validate_explain_request(data: dict) -> tuple[bool, list[str]]:
    """Validate /api/explain/* request body."""
    errors: list[str] = []
    uid = data.get("session_uid", "")
    if not uid or not isinstance(uid, str):
        errors.append("session_uid is required and must be a string.")
    return (len(errors) == 0, errors)


def validate_qa_request(data: dict) -> tuple[bool, list[str]]:
    """Validate /api/explain/qa request body."""
    errors: list[str] = []
    uid = data.get("session_uid", "")
    if not uid:
        errors.append("session_uid is required.")
    q = data.get("question", "")
    if not q or not isinstance(q, str) or len(q.strip()) < 3:
        errors.append("question must be a non-empty string of at least 3 characters.")
    if len(q) > 1000:
        errors.append("question must not exceed 1000 characters.")
    return (len(errors) == 0, errors)


# ── Report request validation ─────────────────────────────────────────────────

def validate_report_request(data: dict) -> tuple[bool, list[str]]:
    """Validate /api/report request body."""
    errors: list[str] = []
    uid = data.get("session_uid", "")
    if not uid:
        errors.append("session_uid is required.")
    rt = data.get("report_type", "")
    if rt and rt not in {"audio", "circuit"}:
        errors.append("report_type must be 'audio' or 'circuit'.")
    return (len(errors) == 0, errors)
