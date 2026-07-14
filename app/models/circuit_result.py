"""
app/models/circuit_result.py  —  CircuitDiagnosticResult ORM model
"""

import json
from app.extensions import db


class CircuitDiagnosticResult(db.Model):
    __tablename__ = "circuit_diagnostics"

    id                      = db.Column(db.Integer,  primary_key=True, autoincrement=True)
    session_id              = db.Column(db.Integer,  db.ForeignKey("analysis_sessions.id",
                                                      ondelete="CASCADE"), nullable=False, index=True)
    # User inputs
    circuit_type            = db.Column(db.String(30), nullable=False)
    op_amp_model            = db.Column(db.String(20), nullable=False)
    supply_voltage_v        = db.Column(db.Float,     nullable=False)
    gain                    = db.Column(db.Float,     nullable=False)
    input_signal_mv         = db.Column(db.Float,     nullable=False)
    signal_freq_hz          = db.Column(db.Float,     nullable=False, default=1000.0)
    observed_issue          = db.Column(db.String(40), nullable=False, default="none")
    # Computed quantities
    expected_output_v       = db.Column(db.Float,     nullable=False)
    output_swing_headroom_v = db.Column(db.Float,     nullable=False)
    # Rule engine outputs
    triggered_rules         = db.Column(db.Text,     nullable=False, default="[]")  # JSON
    rules_triggered_count   = db.Column(db.Integer,  nullable=False, default=0)
    primary_issue           = db.Column(db.Text,     nullable=False, default="")
    root_cause              = db.Column(db.Text,     nullable=False, default="")
    corrective_actions      = db.Column(db.Text,     nullable=False, default="[]")  # JSON
    # Reliability score
    reliability_score       = db.Column(db.Integer,  nullable=False, default=0)
    classification          = db.Column(db.String(20), nullable=False, default="")
    risk_breakdown          = db.Column(db.Text,     nullable=False, default="{}")  # JSON
    score_formula           = db.Column(db.Text,     nullable=True)
    # Calculations snapshot (JSON)
    calculations_snapshot   = db.Column(db.Text,     nullable=True)   # JSON
    # Granite
    granite_explanation     = db.Column(db.Text,     nullable=True)

    session = db.relationship("AnalysisSession", back_populates="circuit_result")

    def to_dict(self) -> dict:
        return {
            "id":             self.id,
            "session_id":     self.session_id,
            "circuit_type":   self.circuit_type,
            "op_amp_model":   self.op_amp_model,
            "supply_voltage_v": self.supply_voltage_v,
            "gain":           self.gain,
            "input_signal_mv": self.input_signal_mv,
            "signal_freq_hz": self.signal_freq_hz,
            "observed_issue": self.observed_issue,
            "calculations":   json.loads(self.calculations_snapshot or "{}"),
            "expected_output_v":       self.expected_output_v,
            "output_headroom_v":       self.output_swing_headroom_v,
            "triggered_rules":         json.loads(self.triggered_rules or "[]"),
            "triggered_count":         self.rules_triggered_count,
            "primary_issue":           self.primary_issue,
            "root_cause":              self.root_cause,
            "corrective_actions":      json.loads(self.corrective_actions or "[]"),
            "reliability": {
                "reliability_score": self.reliability_score,
                "classification":    self.classification,
                "score_formula":     self.score_formula,
                **json.loads(self.risk_breakdown or "{}"),
            },
            "granite_explanation": self.granite_explanation,
        }
