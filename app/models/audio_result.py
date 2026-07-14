"""
app/models/audio_result.py  —  AudioAnalysisResult ORM model
"""

import json
from app.extensions import db


class AudioAnalysisResult(db.Model):
    __tablename__ = "audio_analysis"

    id                   = db.Column(db.Integer,  primary_key=True, autoincrement=True)
    session_id           = db.Column(db.Integer,  db.ForeignKey("analysis_sessions.id",
                                                   ondelete="CASCADE"), nullable=False, index=True)
    filename             = db.Column(db.Text,     nullable=False)
    original_filename    = db.Column(db.Text,     nullable=False)
    file_format          = db.Column(db.String(10), nullable=False)
    sample_rate          = db.Column(db.Integer,  nullable=False)
    duration_sec         = db.Column(db.Float,    nullable=False)
    num_channels         = db.Column(db.Integer,  nullable=False, default=1)
    rms_dbfs             = db.Column(db.Float,    nullable=False)
    peak_amplitude       = db.Column(db.Float,    nullable=False)
    peak_dbfs            = db.Column(db.Float,    nullable=False)
    crest_factor_db      = db.Column(db.Float,    nullable=False)
    snr_db               = db.Column(db.Float,    nullable=False)
    dc_offset            = db.Column(db.Float,    nullable=False, default=0.0)
    dynamic_range_db     = db.Column(db.Float,    nullable=False, default=0.0)
    thd_percent          = db.Column(db.Float,    nullable=True)
    dominant_freqs       = db.Column(db.Text,     nullable=True)   # JSON
    detected_faults      = db.Column(db.Text,     nullable=False, default="[]")  # JSON
    waveform_plot_b64    = db.Column(db.Text,     nullable=True)
    fft_plot_b64         = db.Column(db.Text,     nullable=True)
    spectrogram_plot_b64 = db.Column(db.Text,     nullable=True)
    mel_plot_b64         = db.Column(db.Text,     nullable=True)
    granite_explanation  = db.Column(db.Text,     nullable=True)

    session = db.relationship("AnalysisSession", back_populates="audio_result")

    def to_dict(self, include_plots: bool = True) -> dict:
        d = {
            "id":               self.id,
            "session_id":       self.session_id,
            "filename":         self.filename,
            "original_filename":self.original_filename,
            "file_format":      self.file_format,
            "sample_rate":      self.sample_rate,
            "duration_sec":     self.duration_sec,
            "num_channels":     self.num_channels,
            "metrics": {
                "rms_dbfs":          self.rms_dbfs,
                "peak_amplitude":    self.peak_amplitude,
                "peak_dbfs":         self.peak_dbfs,
                "crest_factor_db":   self.crest_factor_db,
                "snr_db":            self.snr_db,
                "dc_offset":         self.dc_offset,
                "dynamic_range_db":  self.dynamic_range_db,
                "thd_percent":       self.thd_percent,
            },
            "dominant_freqs":    json.loads(self.dominant_freqs or "[]"),
            "faults":            json.loads(self.detected_faults or "[]"),
            "granite_explanation": self.granite_explanation,
        }
        if include_plots:
            d["waveform_plot_b64"]    = self.waveform_plot_b64
            d["fft_plot_b64"]         = self.fft_plot_b64
            d["spectrogram_plot_b64"] = self.spectrogram_plot_b64
            d["mel_plot_b64"]         = self.mel_plot_b64
        return d
