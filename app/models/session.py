"""
app/models/session.py  —  AnalysisSession ORM model
"""

import uuid
from datetime import datetime, timezone

from app.extensions import db


class AnalysisSession(db.Model):
    __tablename__ = "analysis_sessions"

    id           = db.Column(db.Integer,  primary_key=True, autoincrement=True)
    session_uid  = db.Column(db.String(36), nullable=False, unique=True, index=True,
                             default=lambda: str(uuid.uuid4()))
    session_type = db.Column(db.String(20), nullable=False)  # 'audio' | 'circuit'
    created_at   = db.Column(db.DateTime,   nullable=False, index=True,
                             default=lambda: datetime.now(timezone.utc))
    status       = db.Column(db.String(20), nullable=False, default="pending")
                             # 'pending' | 'complete' | 'failed'

    # Relationships
    audio_result   = db.relationship("AudioAnalysisResult",   back_populates="session",
                                      uselist=False, cascade="all, delete-orphan")
    circuit_result = db.relationship("CircuitDiagnosticResult", back_populates="session",
                                      uselist=False, cascade="all, delete-orphan")
    report         = db.relationship("ReportRecord",           back_populates="session",
                                      uselist=False, cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "session_uid":  self.session_uid,
            "session_type": self.session_type,
            "created_at":   self.created_at.isoformat() if self.created_at else None,
            "status":       self.status,
        }
