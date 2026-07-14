"""
app/models/report_record.py  —  ReportRecord ORM model
"""

import uuid
from datetime import datetime, timezone
from app.extensions import db


class ReportRecord(db.Model):
    __tablename__ = "reports"

    id           = db.Column(db.Integer,  primary_key=True, autoincrement=True)
    session_id   = db.Column(db.Integer,  db.ForeignKey("analysis_sessions.id",
                                           ondelete="CASCADE"), nullable=False, index=True)
    report_uid   = db.Column(db.String(36), nullable=False, unique=True,
                              default=lambda: str(uuid.uuid4()))
    file_path    = db.Column(db.Text,     nullable=False)
    file_size_kb = db.Column(db.Integer,  nullable=False, default=0)
    report_type  = db.Column(db.String(20), nullable=False)   # 'audio' | 'circuit'
    generated_at = db.Column(db.DateTime,  nullable=False,
                              default=lambda: datetime.now(timezone.utc))

    session = db.relationship("AnalysisSession", back_populates="report")

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "report_uid":   self.report_uid,
            "file_size_kb": self.file_size_kb,
            "report_type":  self.report_type,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
        }
