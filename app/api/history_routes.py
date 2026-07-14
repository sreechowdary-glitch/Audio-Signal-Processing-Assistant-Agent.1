"""
app/api/history_routes.py
-------------------------
GET /api/history   — paginated session history list
GET /api/health    — application health check
"""

import logging

from flask import Blueprint, request

from app.extensions import db
from app.models.session import AnalysisSession
from app.utils.response_utils import success_response, server_error_response

logger = logging.getLogger(__name__)
history_bp = Blueprint("history", __name__)


@history_bp.route("/history", methods=["GET"])
def get_history():
    """
    GET /api/history
    ----------------
    Return paginated list of completed analysis sessions.

    Query params:
      page     (int, default=1)
      per_page (int, default=20, max=100)
      type     ('audio' | 'circuit' | '' for all)

    Response 200
    ------------
    {
      "success": true,
      "data": {
        "sessions": [...],
        "total": 42,
        "page": 1,
        "per_page": 20,
        "pages": 3
      }
    }
    """
    try:
        page     = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
        stype    = request.args.get("type", "").strip()

        query = AnalysisSession.query.filter(
            AnalysisSession.status == "complete"
        ).order_by(AnalysisSession.created_at.desc())

        if stype in ("audio", "circuit"):
            query = query.filter(AnalysisSession.session_type == stype)

        paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        sessions_out = []
        for s in paginated.items:
            entry = s.to_dict()
            # Attach primary finding for display in history table
            if s.session_type == "audio" and s.audio_result:
                faults = [
                    f["fault"] for f in __import__("json").loads(
                        s.audio_result.detected_faults or "[]"
                    ) if f.get("detected")
                ]
                entry["primary_issue"] = ", ".join(faults) if faults else "No faults"
                entry["filename"]      = s.audio_result.original_filename
            elif s.session_type == "circuit" and s.circuit_result:
                entry["primary_issue"] = s.circuit_result.primary_issue
                entry["reliability_score"] = s.circuit_result.reliability_score
                entry["classification"]    = s.circuit_result.classification
            sessions_out.append(entry)

        return success_response({
            "sessions": sessions_out,
            "total":    paginated.total,
            "page":     page,
            "per_page": per_page,
            "pages":    paginated.pages,
        })
    except Exception as exc:
        logger.error("History query failed: %s", exc)
        return server_error_response("Failed to load session history.")


@history_bp.route("/health", methods=["GET"])
def health_check():
    """
    GET /api/health
    ---------------
    Basic application health check.
    Returns DB connectivity status and Granite availability.
    """
    try:
        db.session.execute(db.text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        from app.ai import check_granite_available
        granite_ok = check_granite_available()
    except Exception:
        granite_ok = False

    return success_response({
        "status":          "ok" if db_ok else "degraded",
        "database":        "ok" if db_ok else "error",
        "granite":         "configured" if granite_ok else "not_configured",
    })
