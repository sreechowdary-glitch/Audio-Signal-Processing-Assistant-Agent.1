"""
app/api/report_routes.py
------------------------
POST /api/report  — Generate and stream a PDF engineering report

Architecture contract
---------------------
This route assembles all pre-computed data from the database and calls
app.reports.pdf_generator — it does NOT re-run any DSP or rule engine logic.
"""

import json
import logging
import os
from pathlib import Path

from flask import Blueprint, current_app, send_file, request

from app.extensions import db
from app.models.session       import AnalysisSession
from app.models.report_record import ReportRecord
from app.utils.validators     import validate_report_request
from app.utils.response_utils import (
    success_response, error_response,
    validation_error_response, server_error_response, not_found_response,
)

logger = logging.getLogger(__name__)
report_bp = Blueprint("report", __name__)


@report_bp.route("/report", methods=["POST"])
def generate_report():
    """
    POST /api/report
    ----------------
    Assemble all pre-computed session results and generate a PDF report.
    Streams the PDF as a file download.

    JSON Body:
    { "session_uid": "...", "report_type": "audio" | "circuit" }
    """
    data  = request.get_json(silent=True) or {}
    valid, errors = validate_report_request(data)
    if not valid:
        return validation_error_response(errors)

    session_uid = data["session_uid"]
    session = AnalysisSession.query.filter_by(
        session_uid=session_uid, status="complete"
    ).first()
    if not session:
        return not_found_response("Session not found or not yet complete.")

    report_type = data.get("report_type") or session.session_type

    # Return cached report if already generated
    if session.report:
        cached_path = Path(session.report.file_path)
        if cached_path.exists():
            logger.info("Serving cached report: %s", cached_path.name)
            return send_file(
                str(cached_path),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"diagnostic_report_{session_uid[:8]}.pdf",
            )

    # Build payload from pre-computed DB records
    try:
        payload = _build_report_payload(session, report_type)
    except ValueError as exc:
        return not_found_response(str(exc))

    # Generate PDF
    try:
        from app.reports.pdf_generator import generate_pdf
        reports_folder = Path(current_app.config["REPORTS_FOLDER"])
        reports_folder.mkdir(parents=True, exist_ok=True)
        pdf_path = reports_folder / f"report_{session_uid[:8]}.pdf"

        generate_pdf(payload, str(pdf_path))

        file_size_kb = int(os.path.getsize(pdf_path) / 1024)

        # Persist report record
        report_rec = ReportRecord(
            session_id=session.id,
            file_path=str(pdf_path),
            file_size_kb=file_size_kb,
            report_type=report_type,
        )
        db.session.add(report_rec)
        db.session.commit()

        logger.info(
            "PDF report generated: %s (%d KB) for session %s",
            pdf_path.name, file_size_kb, session_uid,
        )

        return send_file(
            str(pdf_path),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"diagnostic_report_{session_uid[:8]}.pdf",
        )
    except Exception as exc:
        logger.exception("Report generation failed: %s", exc)
        return server_error_response(f"Report generation failed: {str(exc)}")


def _build_report_payload(session: AnalysisSession, report_type: str) -> dict:
    """
    Assemble all pre-computed data from the database into a single
    payload dict consumed by the PDF generator.
    Raises ValueError if required data is missing.
    """
    payload: dict = {
        "session_uid":  session.session_uid,
        "created_at":   session.created_at.isoformat() if session.created_at else "",
        "report_type":  report_type,
    }

    if report_type == "audio":
        ar = session.audio_result
        if not ar:
            raise ValueError("No audio analysis found for this session.")
        payload["audio"] = ar.to_dict(include_plots=True)

    elif report_type == "circuit":
        cr = session.circuit_result
        if not cr:
            raise ValueError("No circuit diagnostic found for this session.")
        payload["circuit"] = cr.to_dict()

    else:
        raise ValueError(f"Unknown report_type: {report_type}")

    return payload
