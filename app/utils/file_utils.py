"""
app/utils/file_utils.py
-----------------------
Secure file handling utilities.

Responsibilities:
- Generate UUID-based safe filenames (prevents path traversal)
- Save uploaded files to the upload folder
- Clean up temporary files
- Ensure output directories exist
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def secure_save(file_storage, upload_folder: Path) -> tuple[Path, str]:
    """
    Save a Werkzeug FileStorage object to the upload folder with a UUID filename.

    Parameters
    ----------
    file_storage  : werkzeug.datastructures.FileStorage
    upload_folder : destination directory

    Returns
    -------
    (saved_path, safe_filename)
    """
    upload_folder = Path(upload_folder)
    upload_folder.mkdir(parents=True, exist_ok=True)

    original_name = Path(file_storage.filename).name
    ext = Path(original_name).suffix.lower()
    safe_name = f"{uuid.uuid4().hex}{ext}"
    dest = upload_folder / safe_name

    file_storage.save(str(dest))
    logger.info("Saved upload: %s -> %s (%d bytes)", original_name, safe_name, dest.stat().st_size)
    return dest, safe_name


def delete_file(path: Path | str) -> bool:
    """Delete a file, returning True on success."""
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except Exception as exc:
        logger.warning("Could not delete file %s: %s", path, exc)
        return False


def format_bytes(n: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
