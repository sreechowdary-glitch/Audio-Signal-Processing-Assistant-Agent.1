"""
app/api/audio_routes.py
-----------------------
POST /api/analyze

Architecture contract
---------------------
This route is a THIN ORCHESTRATOR only:
  1. Validate the uploaded file
  2. Save to disk securely
  3. Call app.dsp.run_full_analysis()  ← ALL DSP computation happens here
  4. Persist results to SQLite
  5. Optionally call Granite explanation
  6. Return structured JSON

Zero DSP logic lives here. Zero engineering calculations live here.
"""

import json
import logging
from pathlib import Path

from flask import Blueprint, current_app, request

from app.extensions import db
from app.models.session      import AnalysisSession
from app.models.audio_result import AudioAnalysisResult
from app.utils.validators    import validate_audio_file
from app.utils.file_utils    import secure_save
from app.utils.response_utils import (
    success_response, error_response,
    validation_error_response, server_error_response,
)

logger = logging.getLogger(__name__)
audio_bp = Blueprint("audio", __name__)


@audio_bp.route("/analyze", methods=["POST"])
def analyze_audio():
    """
    POST /api/analyze
    -----------------
    Receive audio file upload, run full DSP pipeline, return results.

    Form Data
    ---------
    audio_file : multipart file (WAV, MP3, FLAC)

    Response 200
    ------------
    {
      "success": true,
      "data": {
        "session_uid": "...",
        "filename": "...",
        "sample_rate": 44100,
        "duration_sec": 3.74,
        "metrics": { rms_dbfs, peak_amplitude, snr_db, ... },
        "dominant_freqs": [...],
        "faults": [...],
        "waveform_plot_b64": "...",
        "fft_plot_b64": "...",
        "spectrogram_plot_b64": "...",
        "mel_plot_b64": "..."
      }
    }
    """
    # ── 1. Validate uploaded file ─────────────────────────────────────────
    file = request.files.get("audio_file")
    valid, errors = validate_audio_file(file)
    if not valid:
        return validation_error_response(errors, "Invalid audio file")

    # ── 2. Save to uploads/ ───────────────────────────────────────────────
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    try:
        saved_path, safe_name = secure_save(file, upload_folder)
        original_name = Path(file.filename).name
    except Exception as exc:
        logger.error("File save failed: %s", exc)
        return server_error_response("Could not save uploaded file.")

    # ── 3. Create session record ──────────────────────────────────────────
    session = AnalysisSession(session_type="audio", status="pending")
    db.session.add(session)
    db.session.flush()   # get session.id without committing

    saved_file_path = None
    try:
        # ── 4. Run full DSP pipeline (all computation in app.dsp) ─────────
        from app.dsp import run_full_analysis
        result = run_full_analysis(saved_path)
        saved_file_path = saved_path

        audio_signal = result.audio
        metrics      = result.metrics
        fft          = result.fft
        spectrogram  = result.spectrogram
        faults       = result.faults

        # ── 5. Extract THD from fault results ─────────────────────────────
        thd = next(
            (f.detail.get("thd_percent") for f in faults
             if f.fault == "Distortion (THD)"),
            None,
        )

        # ── 6. Persist AudioAnalysisResult ────────────────────────────────
        audio_record = AudioAnalysisResult(
            session_id=session.id,
            filename=safe_name,
            original_filename=original_name,
            file_format=audio_signal.file_format,
            sample_rate=audio_signal.sample_rate,
            duration_sec=audio_signal.duration_sec,
            num_channels=audio_signal.num_channels,
            rms_dbfs=metrics.rms_dbfs,
            peak_amplitude=metrics.peak_amplitude,
            peak_dbfs=metrics.peak_dbfs,
            crest_factor_db=metrics.crest_factor_db,
            snr_db=metrics.snr_db,
            dc_offset=metrics.dc_offset,
            dynamic_range_db=metrics.dynamic_range_db,
            thd_percent=thd,
            dominant_freqs=json.dumps([
                {"freq_hz": p.freq_hz, "magnitude_db": p.magnitude_db}
                for p in fft.dominant_freqs[:5]
            ]),
            detected_faults=json.dumps([
                {
                    "fault":       f.fault,
                    "detected":    f.detected,
                    "confidence":  f.confidence,
                    "severity":    f.severity,
                    "explanation": f.explanation,
                    "detail":      f.detail,
                }
                for f in faults
            ]),
            waveform_plot_b64=result.waveform.plot_b64,
            fft_plot_b64=fft.plot_b64,
            spectrogram_plot_b64=spectrogram.stft_plot_b64,
            mel_plot_b64=spectrogram.mel_plot_b64,
        )
        db.session.add(audio_record)
        session.status = "complete"
        db.session.commit()

        logger.info(
            "Audio analysis complete: session=%s file=%s sr=%d dur=%.2fs faults=%s",
            session.session_uid, original_name, audio_signal.sample_rate,
            audio_signal.duration_sec,
            [f.fault for f in faults if f.detected],
        )

        # ── 7. Build response payload ─────────────────────────────────────
        response_data = {
            **audio_record.to_dict(include_plots=True),
            "session_uid": session.session_uid,
        }
        return success_response(response_data, "Audio analysis complete")

    except Exception as exc:
        logger.exception("Audio analysis pipeline failed: %s", exc)
        session.status = "failed"
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        # Clean up saved file on error
        if saved_file_path:
            from app.utils.file_utils import delete_file
            delete_file(saved_file_path)
        return server_error_response(f"Analysis failed: {str(exc)}")
