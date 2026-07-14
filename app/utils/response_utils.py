"""
app/utils/response_utils.py
---------------------------
Standard JSON response envelope builders.

Every API endpoint returns one of these shapes:

  Success:
    { "success": true,  "data": {...},  "message": "OK" }

  Error:
    { "success": false, "data": null,   "message": "...", "errors": [...] }

Using consistent envelopes means the frontend JS only needs to check
json.success — it never has to guess the response shape.
"""

from flask import jsonify
from typing import Any


def success_response(data: Any = None, message: str = "OK", status: int = 200):
    """Return a 2xx JSON success response."""
    return jsonify({"success": True, "data": data, "message": message}), status


def error_response(message: str, status: int = 400, errors: list | None = None):
    """Return a 4xx / 5xx JSON error response."""
    return jsonify({
        "success": False,
        "data":    None,
        "message": message,
        "errors":  errors or [],
    }), status


def validation_error_response(errors: list[str], message: str = "Validation failed"):
    """Return a 422 Unprocessable Entity for input validation failures."""
    return jsonify({
        "success": False,
        "data":    None,
        "message": message,
        "errors":  errors,
    }), 422


def not_found_response(message: str = "Resource not found"):
    """Return a 404 JSON response."""
    return error_response(message, 404)


def server_error_response(message: str = "Internal server error"):
    """Return a 500 JSON response."""
    return error_response(message, 500)
