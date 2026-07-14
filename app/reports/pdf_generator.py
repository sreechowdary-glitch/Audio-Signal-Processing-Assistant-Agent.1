"""
app/reports/pdf_generator.py
─────────────────────────────────────────────────────────────────────────────
Step 10 — Full PDF Engineering Report Generator

Architecture contract
─────────────────────
• Consumes pre-computed payload dict assembled by report_routes._build_report_payload()
• Uses ReportLab (Platypus) for layout + Base64 PNG plots from the DSP engine
• Does NOT call any DSP, rule engine, or Granite — pure presentation layer
• generate_pdf(payload, output_path) is the single public entry point

Payload structure accepted
──────────────────────────
{
  "session_uid": str,
  "created_at":  str (ISO),
  "report_type": "audio" | "circuit",

  # if report_type == "audio"
  "audio": {
      "original_filename": str,
      "file_format": str,
      "sample_rate": int,
      "duration_sec": float,
      "num_channels": int,
      "metrics": { rms_dbfs, peak_amplitude, peak_dbfs, crest_factor_db,
                   snr_db, dc_offset, dynamic_range_db, thd_percent },
      "dominant_freqs": [ {"frequency_hz": ..., "magnitude_db": ...} ],
      "faults": [ {"fault_type": ..., "confidence": ..., "severity": ...,
                   "explanation": ...} ],
      "waveform_plot_b64": str | None,
      "fft_plot_b64": str | None,
      "spectrogram_plot_b64": str | None,
      "mel_plot_b64": str | None,
      "granite_explanation": str | None,
  },

  # if report_type == "circuit"
  "circuit": {
      "circuit_type": str,
      "op_amp_model": str,
      "supply_voltage_v": float,
      "gain": float,
      "input_signal_mv": float,
      "signal_freq_hz": float,
      "observed_issue": str,
      "expected_output_v": float,
      "output_headroom_v": float,
      "calculations": { ... },
      "triggered_rules": [ {"rule_id": ..., "name": ..., "severity": ...,
                            "message": ..., "corrective_action": ...} ],
      "triggered_count": int,
      "primary_issue": str,
      "root_cause": str,
      "corrective_actions": [str],
      "reliability": {
          "reliability_score": int,
          "classification": str,
          "score_formula": str,
          "power_margin_score": float,
          "stability_score": float,
          "noise_score": float,
          "distortion_score": float,
      },
      "granite_explanation": str | None,
  }
}
"""

from __future__ import annotations

import base64
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── ReportLab imports (fail loudly — it should always be installed) ───────────
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import (
    HexColor, black, white, Color,
    darkblue, lightgrey, grey
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, HRFlowable, PageBreak, KeepTogether
)
from reportlab.platypus import Flowable


# ─── Brand colours ────────────────────────────────────────────────────────────
IBM_BLUE       = HexColor("#0f62fe")
IBM_DARK       = HexColor("#161616")
IBM_COOL_GREY  = HexColor("#f4f4f4")
IBM_MID_GREY   = HexColor("#e0e0e0")
IBM_TEXT_GREY  = HexColor("#525252")
IBM_WHITE      = HexColor("#ffffff")
SEVERITY_CRIT  = HexColor("#da1e28")   # Red
SEVERITY_HIGH  = HexColor("#ff832b")   # Orange
SEVERITY_MED   = HexColor("#f1c21b")   # Yellow
SEVERITY_LOW   = HexColor("#198038")   # Green
SCORE_GOOD     = HexColor("#198038")   # ≥80
SCORE_FAIR     = HexColor("#f1c21b")   # 60–79
SCORE_POOR     = HexColor("#da1e28")   # <60

PAGE_W, PAGE_H = A4
MARGIN = 2.2 * cm


# ─── Custom flowable: coloured score badge ────────────────────────────────────
class ScoreBadge(Flowable):
    """Draws a filled rounded-rect badge displaying the reliability score."""

    def __init__(self, score: int, classification: str, width=120, height=64):
        super().__init__()
        self.score = score
        self.classification = classification
        self.width = width
        self.height = height

    def draw(self):
        c = self.canv
        r = 8  # corner radius
        if self.score >= 80:
            bg = SCORE_GOOD
        elif self.score >= 60:
            bg = SCORE_FAIR
        else:
            bg = SCORE_POOR
        c.setFillColor(bg)
        c.roundRect(0, 0, self.width, self.height, r, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 28)
        c.drawCentredString(self.width / 2, self.height - 38, str(self.score))
        c.setFont("Helvetica", 9)
        c.drawCentredString(self.width / 2, 10, self.classification.upper())


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _b64_to_image(b64_string: str, max_width: float, max_height: float) -> Image | None:
    """Decode a base64 PNG string to a ReportLab Image object."""
    if not b64_string:
        return None
    try:
        raw = base64.b64decode(b64_string)
        buf = io.BytesIO(raw)
        img = Image(buf)
        scale = min(max_width / img.drawWidth, max_height / img.drawHeight)
        img.drawWidth  *= scale
        img.drawHeight *= scale
        return img
    except Exception as exc:
        logger.warning("Could not decode plot image: %s", exc)
        return None


def _fmt_float(val: Any, decimals: int = 3, suffix: str = "") -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(val)


def _severity_colour(severity: str) -> HexColor:
    s = (severity or "").lower()
    if s == "critical":
        return SEVERITY_CRIT
    if s == "high":
        return SEVERITY_HIGH
    if s == "medium":
        return SEVERITY_MED
    return SEVERITY_LOW


def _circuit_type_label(ct: str) -> str:
    labels = {
        "non_inverting":    "Non-Inverting Amplifier",
        "inverting":        "Inverting Amplifier",
        "unity_gain":       "Unity Gain (Voltage Follower)",
        "differential":     "Differential Amplifier",
        "instrumentation":  "Instrumentation Amplifier",
        "integrator":       "Integrator",
        "differentiator":   "Differentiator",
    }
    return labels.get(ct, ct.replace("_", " ").title())


# ─── Style factory ────────────────────────────────────────────────────────────

def _make_styles() -> dict:
    base = getSampleStyleSheet()

    def P(name, **kw) -> ParagraphStyle:
        parent = kw.pop("parent", "Normal")
        return ParagraphStyle(name, parent=base[parent], **kw)

    return {
        "cover_title": P("cover_title",
            fontSize=22, fontName="Helvetica-Bold",
            textColor=IBM_BLUE, spaceAfter=6, leading=26),
        "cover_sub": P("cover_sub",
            fontSize=13, fontName="Helvetica",
            textColor=IBM_DARK, spaceAfter=4),
        "cover_meta": P("cover_meta",
            fontSize=9, fontName="Helvetica",
            textColor=IBM_TEXT_GREY, spaceAfter=3),
        "section_head": P("section_head",
            fontSize=13, fontName="Helvetica-Bold",
            textColor=IBM_BLUE, spaceBefore=14, spaceAfter=4, leading=16),
        "sub_head": P("sub_head",
            fontSize=11, fontName="Helvetica-Bold",
            textColor=IBM_DARK, spaceBefore=8, spaceAfter=3, leading=13),
        "body": P("body",
            fontSize=9, fontName="Helvetica",
            textColor=IBM_DARK, leading=13, spaceAfter=3),
        "body_bold": P("body_bold",
            fontSize=9, fontName="Helvetica-Bold",
            textColor=IBM_DARK, leading=13),
        "caption": P("caption",
            fontSize=8, fontName="Helvetica-Oblique",
            textColor=IBM_TEXT_GREY, alignment=TA_CENTER, spaceAfter=4),
        "table_head": P("table_head",
            fontSize=8, fontName="Helvetica-Bold",
            textColor=IBM_WHITE, leading=10),
        "table_cell": P("table_cell",
            fontSize=8, fontName="Helvetica",
            textColor=IBM_DARK, leading=10),
        "table_cell_b": P("table_cell_b",
            fontSize=8, fontName="Helvetica-Bold",
            textColor=IBM_DARK, leading=10),
        "footer_txt": P("footer_txt",
            fontSize=7, fontName="Helvetica",
            textColor=IBM_TEXT_GREY, alignment=TA_CENTER),
        "granite_head": P("granite_head",
            fontSize=10, fontName="Helvetica-Bold",
            textColor=IBM_BLUE, spaceBefore=6, spaceAfter=2),
        "granite_body": P("granite_body",
            fontSize=9, fontName="Helvetica",
            textColor=IBM_DARK, leading=13, spaceAfter=2,
            alignment=TA_JUSTIFY),
        "warn_box": P("warn_box",
            fontSize=9, fontName="Helvetica",
            textColor=IBM_DARK, leading=13),
    }


# ─── Page decorations (header/footer on every page) ───────────────────────────

class _PageTemplate:
    """Injected into the doc via onFirstPage / onLaterPages."""

    def __init__(self, session_uid: str, report_title: str):
        self._uid   = session_uid
        self._title = report_title

    def on_page(self, canvas, doc):
        canvas.saveState()
        w, h = A4
        # Top rule
        canvas.setStrokeColor(IBM_BLUE)
        canvas.setLineWidth(1.5)
        canvas.line(MARGIN, h - 1.4 * cm, w - MARGIN, h - 1.4 * cm)
        # Header text
        canvas.setFont("Helvetica-Bold", 7)
        canvas.setFillColor(IBM_BLUE)
        canvas.drawString(MARGIN, h - 1.1 * cm, "AUDIO INTELLIGENCE & CIRCUIT DIAGNOSTIC PLATFORM")
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(IBM_TEXT_GREY)
        canvas.drawRightString(w - MARGIN, h - 1.1 * cm, self._title)
        # Bottom rule
        canvas.setStrokeColor(IBM_MID_GREY)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, 1.4 * cm, w - MARGIN, 1.4 * cm)
        # Footer text
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(IBM_TEXT_GREY)
        page_num = f"Page {doc.page}"
        canvas.drawCentredString(w / 2, 0.9 * cm, page_num)
        canvas.drawString(MARGIN, 0.9 * cm, f"Session: {self._uid[:8]}")
        canvas.drawRightString(w - MARGIN,  0.9 * cm,
            "IBM SkillsBuild | Edunet Foundation Internship")
        canvas.restoreState()


# ─── Section builders ─────────────────────────────────────────────────────────

def _cover_page(styles: dict, payload: dict) -> list:
    """Cover page flowables."""
    el: list = []
    el.append(Spacer(1, 1.8 * cm))

    title = ("Audio Signal Analysis Report"
             if payload["report_type"] == "audio"
             else "Circuit Diagnostic Engineering Report")
    el.append(Paragraph("Audio Intelligence &amp; Circuit Diagnostic Platform", styles["cover_title"]))
    el.append(Paragraph(title, styles["cover_sub"]))
    el.append(HRFlowable(width="100%", thickness=1.5, color=IBM_BLUE, spaceAfter=10))

    created = payload.get("created_at", "")
    try:
        dt = datetime.fromisoformat(created)
        created_str = dt.strftime("%d %B %Y, %H:%M UTC")
    except Exception:
        created_str = created or "N/A"

    meta_rows = [
        ("Session ID",    payload.get("session_uid", "N/A")),
        ("Report Type",   payload["report_type"].upper()),
        ("Generated",     created_str),
        ("Platform",      "Audio Intelligence & Circuit Diagnostic Platform v1.0"),
        ("Prepared for",  "IBM SkillsBuild × Edunet Foundation Internship Program"),
    ]
    if payload["report_type"] == "audio" and "audio" in payload:
        a = payload["audio"]
        meta_rows.insert(1, ("Audio File", a.get("original_filename", "N/A")))
    elif payload["report_type"] == "circuit" and "circuit" in payload:
        c = payload["circuit"]
        meta_rows.insert(1, ("Circuit",
            f"{_circuit_type_label(c.get('circuit_type',''))} / {c.get('op_amp_model','')}"))

    tdata = [[Paragraph(f"<b>{k}</b>", styles["body_bold"]),
              Paragraph(v, styles["body"])] for k, v in meta_rows]
    t = Table(tdata, colWidths=[4.5 * cm, 11 * cm])
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [IBM_COOL_GREY, IBM_WHITE]),
        ("BOX",            (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("INNERGRID",      (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
    ]))
    el.append(t)
    el.append(Spacer(1, 1.2 * cm))

    # Disclaimer box
    disclaimer = (
        "This report was generated by the Audio Intelligence &amp; Circuit Diagnostic Platform. "
        "All DSP metrics, fault detection, and circuit diagnostics were computed by the engineering "
        "analysis engines. IBM Granite AI was used exclusively to provide natural-language "
        "explanations of the pre-computed engineering results. "
        "This report is intended for educational and diagnostic purposes."
    )
    disc_data = [[Paragraph("<b>DISCLAIMER</b>", styles["body_bold"]),
                  Paragraph(disclaimer, styles["body"])]]
    disc_t = Table(disc_data, colWidths=[2.5 * cm, 13 * cm])
    disc_t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), HexColor("#edf5ff")),
        ("BOX",           (0, 0), (-1, -1), 0.8, IBM_BLUE),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    el.append(disc_t)
    el.append(PageBreak())
    return el


# ── Audio sections ─────────────────────────────────────────────────────────────

def _audio_file_info(styles: dict, a: dict) -> list:
    el: list = [Paragraph("1. Audio File Information", styles["section_head"])]
    rows = [
        ("Filename",          a.get("original_filename", "N/A")),
        ("Format",            (a.get("file_format") or "N/A").upper()),
        ("Sample Rate",       f"{a.get('sample_rate', 0):,} Hz"),
        ("Duration",          _fmt_float(a.get("duration_sec"), 3, " s")),
        ("Channels",          str(a.get("num_channels", 1))),
    ]
    tdata = [[Paragraph(f"<b>{k}</b>", styles["table_cell_b"]),
              Paragraph(v, styles["table_cell"])] for k, v in rows]
    t = Table(tdata, colWidths=[5 * cm, 10.5 * cm])
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [IBM_COOL_GREY, IBM_WHITE]),
        ("BOX",           (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
    ]))
    el.append(t)
    return el


def _audio_metrics(styles: dict, a: dict) -> list:
    el: list = [Paragraph("2. Signal Quality Metrics", styles["section_head"])]
    m = a.get("metrics", {})
    rows = [
        ("RMS Level",            _fmt_float(m.get("rms_dbfs"),        2, " dBFS")),
        ("Peak Amplitude",       _fmt_float(m.get("peak_amplitude"),  6)),
        ("Peak Level",           _fmt_float(m.get("peak_dbfs"),       2, " dBFS")),
        ("Crest Factor",         _fmt_float(m.get("crest_factor_db"), 2, " dB")),
        ("Signal-to-Noise Ratio",_fmt_float(m.get("snr_db"),          2, " dB")),
        ("DC Offset",            _fmt_float(m.get("dc_offset"),       6)),
        ("Dynamic Range",        _fmt_float(m.get("dynamic_range_db"),2, " dB")),
        ("THD",                  _fmt_float(m.get("thd_percent"),     3, " %")
            if m.get("thd_percent") is not None else "N/A"),
    ]
    header = [Paragraph("<b>Metric</b>", styles["table_head"]),
              Paragraph("<b>Value</b>",  styles["table_head"])]
    tdata = [header] + [
        [Paragraph(k, styles["table_cell_b"]),
         Paragraph(v, styles["table_cell"])] for k, v in rows
    ]
    t = Table(tdata, colWidths=[7 * cm, 8.5 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), IBM_BLUE),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [IBM_COOL_GREY, IBM_WHITE]),
        ("BOX",           (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
    ]))
    el.append(t)

    # Dominant frequencies sub-table
    dom = a.get("dominant_freqs", [])
    if dom:
        el.append(Spacer(1, 6))
        el.append(Paragraph("Dominant Frequencies", styles["sub_head"]))
        fheader = [Paragraph("<b>#</b>",       styles["table_head"]),
                   Paragraph("<b>Freq (Hz)</b>",styles["table_head"]),
                   Paragraph("<b>Magnitude (dB)</b>", styles["table_head"])]
        fdata = [fheader]
        for i, f in enumerate(dom[:10], 1):
            fdata.append([
                Paragraph(str(i), styles["table_cell"]),
                Paragraph(_fmt_float(f.get("frequency_hz"), 2), styles["table_cell"]),
                Paragraph(_fmt_float(f.get("magnitude_db"), 2), styles["table_cell"]),
            ])
        ft = Table(fdata, colWidths=[1.5 * cm, 5 * cm, 9 * cm])
        ft.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), IBM_BLUE),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [IBM_COOL_GREY, IBM_WHITE]),
            ("BOX",           (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
            ("INNERGRID",     (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ]))
        el.append(ft)
    return el


def _audio_plots(styles: dict, a: dict) -> list:
    el: list = [Paragraph("3. Signal Visualisations", styles["section_head"])]
    plot_defs = [
        ("waveform_plot_b64",    "Figure 3.1 — Time-Domain Waveform"),
        ("fft_plot_b64",         "Figure 3.2 — FFT Frequency Spectrum"),
        ("spectrogram_plot_b64", "Figure 3.3 — Short-Time Fourier Transform Spectrogram"),
        ("mel_plot_b64",         "Figure 3.4 — Mel-Scale Spectrogram"),
    ]
    W = PAGE_W - 2 * MARGIN
    any_plot = False
    for key, caption in plot_defs:
        img = _b64_to_image(a.get(key), max_width=W, max_height=7 * cm)
        if img:
            any_plot = True
            el.append(KeepTogether([img, Paragraph(caption, styles["caption"])]))
            el.append(Spacer(1, 4))
    if not any_plot:
        el.append(Paragraph(
            "<i>No plot images available. Audio plots are embedded when analysis is run "
            "through the full pipeline with a valid audio file.</i>",
            styles["body"]))
    return el


def _audio_faults(styles: dict, a: dict) -> list:
    el: list = [Paragraph("4. Fault Detection Results", styles["section_head"])]
    faults = a.get("faults", [])
    if not faults:
        el.append(Paragraph("No faults detected in this audio signal.", styles["body"]))
        return el

    header = [
        Paragraph("<b>Fault Type</b>",  styles["table_head"]),
        Paragraph("<b>Severity</b>",    styles["table_head"]),
        Paragraph("<b>Confidence</b>",  styles["table_head"]),
        Paragraph("<b>Engineering Explanation</b>", styles["table_head"]),
    ]
    tdata = [header]
    for f in faults:
        sev = f.get("severity", "Low")
        tdata.append([
            Paragraph(f.get("fault_type", "").replace("_", " ").title(), styles["table_cell_b"]),
            Paragraph(sev,  styles["table_cell"]),
            Paragraph(f"{float(f.get('confidence', 0)) * 100:.0f}%", styles["table_cell"]),
            Paragraph(f.get("explanation", "")[:180], styles["table_cell"]),
        ])
    t = Table(tdata, colWidths=[3.5 * cm, 2 * cm, 2.5 * cm, 7.5 * cm])
    row_colours = []
    for i, f in enumerate(faults, 1):
        sev = f.get("severity", "Low")
        c = _severity_colour(sev)
        row_colours.append(("BACKGROUND", (1, i), (1, i), c))
        row_colours.append(("TEXTCOLOR",  (1, i), (1, i), white))

    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), IBM_BLUE),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [IBM_COOL_GREY, IBM_WHITE]),
        ("BOX",           (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        *row_colours,
    ]))
    el.append(t)
    return el


# ── Circuit sections ───────────────────────────────────────────────────────────

def _circuit_inputs(styles: dict, c: dict) -> list:
    el: list = [Paragraph("1. Circuit Configuration", styles["section_head"])]
    rows = [
        ("Circuit Topology",    _circuit_type_label(c.get("circuit_type", ""))),
        ("Op-Amp Model",        c.get("op_amp_model", "N/A")),
        ("Supply Voltage",      f"+/-{c.get('supply_voltage_v', 0):.1f} V"),
        ("Gain (Av)",           f"{c.get('gain', 0):.1f} V/V"),
        ("Input Signal",        f"{c.get('input_signal_mv', 0):.1f} mV"),
        ("Signal Frequency",    f"{c.get('signal_freq_hz', 0):.1f} Hz"),
        ("Observed Issue",      c.get("observed_issue", "none").replace("_", " ").title()),
    ]
    tdata = [[Paragraph(f"<b>{k}</b>", styles["table_cell_b"]),
              Paragraph(v, styles["table_cell"])] for k, v in rows]
    t = Table(tdata, colWidths=[5 * cm, 10.5 * cm])
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [IBM_COOL_GREY, IBM_WHITE]),
        ("BOX",           (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
    ]))
    el.append(t)
    return el


def _circuit_calcs(styles: dict, c: dict) -> list:
    el: list = [Paragraph("2. Engineering Calculations", styles["section_head"])]
    exp_out = c.get("expected_output_v", 0)
    headroom = c.get("output_headroom_v", 0)
    rows = [
        ("Expected Output Voltage",  f"{exp_out:.3f} V"),
        ("Output Swing Headroom",    f"{headroom:.3f} V"),
    ]
    # Flatten calculations snapshot
    calcs = c.get("calculations", {})
    if calcs:
        for k, v in calcs.items():
            if isinstance(v, (int, float)):
                rows.append((k.replace("_", " ").title(), _fmt_float(v, 4)))
            elif isinstance(v, str):
                rows.append((k.replace("_", " ").title(), v))

    tdata = [[Paragraph(f"<b>{k}</b>", styles["table_cell_b"]),
              Paragraph(str(v), styles["table_cell"])] for k, v in rows]
    t = Table(tdata, colWidths=[8 * cm, 7.5 * cm])
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [IBM_COOL_GREY, IBM_WHITE]),
        ("BOX",           (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
    ]))
    el.append(t)
    return el


def _circuit_reliability_score(styles: dict, c: dict) -> list:
    el: list = [Paragraph("3. Circuit Reliability Score", styles["section_head"])]
    rel = c.get("reliability", {})
    score  = int(rel.get("reliability_score", 0))
    cls    = rel.get("classification", "Unknown")
    formula = rel.get("score_formula", "")

    # Badge + summary side-by-side
    badge = ScoreBadge(score, cls)
    summary_rows = [
        ("Classification",   cls),
        ("Power Margin",     _fmt_float(rel.get("power_margin_score"), 0, " / 100")),
        ("Stability",        _fmt_float(rel.get("stability_score"),    0, " / 100")),
        ("Noise",            _fmt_float(rel.get("noise_score"),        0, " / 100")),
        ("Distortion",       _fmt_float(rel.get("distortion_score"),   0, " / 100")),
    ]
    summary_tdata = [[Paragraph(f"<b>{k}</b>", styles["table_cell_b"]),
                      Paragraph(v, styles["table_cell"])] for k, v in summary_rows]
    summary_t = Table(summary_tdata, colWidths=[4.5 * cm, 5 * cm])
    summary_t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [IBM_COOL_GREY, IBM_WHITE]),
        ("BOX",           (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
    ]))
    outer = Table([[badge, summary_t]], colWidths=[3.5 * cm, 12 * cm])
    outer.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    el.append(outer)

    if formula:
        el.append(Spacer(1, 4))
        el.append(Paragraph(
            f"<b>Score Formula:</b> {formula}", styles["body"]))
    return el


def _circuit_rules(styles: dict, c: dict) -> list:
    el: list = [Paragraph("4. Rule Engine — Triggered Violations", styles["section_head"])]
    rules = c.get("triggered_rules", [])
    primary = c.get("primary_issue", "")
    root    = c.get("root_cause", "")
    actions = c.get("corrective_actions", [])

    if primary:
        el.append(Paragraph(f"<b>Primary Issue Detected:</b> {primary}", styles["body_bold"]))
    if root:
        el.append(Paragraph(f"<b>Root Cause:</b> {root}", styles["body"]))
    el.append(Spacer(1, 4))

    if not rules:
        el.append(Paragraph("No rule violations detected. Circuit parameters are within "
                             "acceptable engineering limits.", styles["body"]))
    else:
        header = [
            Paragraph("<b>Rule ID</b>",    styles["table_head"]),
            Paragraph("<b>Rule Name</b>",  styles["table_head"]),
            Paragraph("<b>Severity</b>",   styles["table_head"]),
            Paragraph("<b>Violation &amp; Corrective Action</b>", styles["table_head"]),
        ]
        tdata = [header]
        sev_styles = []
        for i, r in enumerate(rules, 1):
            sev = r.get("severity", "low")
            sev_styles.append(("BACKGROUND", (2, i), (2, i), _severity_colour(sev)))
            sev_styles.append(("TEXTCOLOR",  (2, i), (2, i), white))
            msg    = r.get("message", "")
            action = r.get("corrective_action", "")
            cell_text = msg
            if action:
                cell_text += f"<br/><i>Action: {action}</i>"
            tdata.append([
                Paragraph(r.get("rule_id", ""), styles["table_cell_b"]),
                Paragraph(r.get("name", ""),    styles["table_cell"]),
                Paragraph(sev.upper(),          styles["table_cell"]),
                Paragraph(cell_text,            styles["table_cell"]),
            ])
        t = Table(tdata, colWidths=[1.8 * cm, 4 * cm, 2.0 * cm, 7.7 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), IBM_BLUE),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [IBM_COOL_GREY, IBM_WHITE]),
            ("BOX",           (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
            ("INNERGRID",     (0, 0), (-1, -1), 0.5, IBM_MID_GREY),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            *sev_styles,
        ]))
        el.append(t)

    if actions:
        el.append(Spacer(1, 6))
        el.append(Paragraph("Corrective Actions Summary", styles["sub_head"]))
        for i, act in enumerate(actions, 1):
            el.append(Paragraph(f"{i}. {act}", styles["body"]))
    return el


# ── Shared: IBM Granite explanation section ────────────────────────────────────

def _granite_section(styles: dict, explanation: str | None, section_num: str) -> list:
    el: list = [Paragraph(
        f"{section_num}. IBM Granite AI — Engineering Explanation",
        styles["section_head"])]

    if not explanation:
        box_data = [[Paragraph(
            "<b>IBM Granite Offline</b><br/>"
            "Granite AI credentials are not configured. Configure IBM_WATSONX_API_KEY, "
            "IBM_WATSONX_PROJECT_ID, and IBM_WATSONX_URL in your .env file to receive "
            "natural-language engineering explanations. All diagnostic results above were "
            "generated entirely by the rule-based engineering engines.",
            styles["warn_box"]
        )]]
        box_t = Table(box_data, colWidths=[15.5 * cm])
        box_t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), HexColor("#fff3cd")),
            ("BOX",           (0, 0), (-1, -1), 0.8, HexColor("#f1c21b")),
            ("TOPPADDING",    (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ]))
        el.append(box_t)
        return el

    el.append(Paragraph(
        "<i>The following explanation was generated by IBM Granite AI based on pre-computed "
        "engineering results. Granite did not perform any calculations.</i>",
        styles["caption"]))
    el.append(Spacer(1, 4))

    # Split by common section markers
    import re
    parts = re.split(r"\n(?=[A-Z][^a-z]{2,}:|\d+\.\s)", explanation.strip())
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if re.match(r"^[A-Z][^a-z]{2,}:|^\d+\.\s[A-Z]", part):
            lines = part.split("\n", 1)
            el.append(Paragraph(lines[0], styles["granite_head"]))
            if len(lines) > 1:
                el.append(Paragraph(lines[1].strip(), styles["granite_body"]))
        else:
            el.append(Paragraph(part, styles["granite_body"]))
    return el


# ─── Main entry point ─────────────────────────────────────────────────────────

def generate_pdf(payload: dict, output_path: str) -> str:
    """
    Generate a full engineering PDF report and write it to output_path.

    Parameters
    ----------
    payload     : dict  — assembled by report_routes._build_report_payload()
    output_path : str   — absolute path to write the PDF

    Returns
    -------
    str  — the output_path (for chaining)
    """
    report_type = payload.get("report_type", "audio")
    session_uid = payload.get("session_uid", "unknown")

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    report_title = (
        "Audio Signal Analysis Report"
        if report_type == "audio"
        else "Circuit Diagnostic Engineering Report"
    )

    page_tpl = _PageTemplate(session_uid, report_title)
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        title=report_title,
        author="Audio Intelligence & Circuit Diagnostic Platform",
        subject=f"Session {session_uid[:8]}",
    )

    styles = _make_styles()
    story: list = []

    # Cover page
    story.extend(_cover_page(styles, payload))

    if report_type == "audio":
        a = payload.get("audio", {})
        story.extend(_audio_file_info(styles, a))
        story.append(Spacer(1, 6))
        story.extend(_audio_metrics(styles, a))
        story.append(PageBreak())
        story.extend(_audio_plots(styles, a))
        story.append(PageBreak())
        story.extend(_audio_faults(styles, a))
        story.append(Spacer(1, 8))
        story.extend(_granite_section(styles, a.get("granite_explanation"), "5"))

    elif report_type == "circuit":
        c = payload.get("circuit", {})
        story.extend(_circuit_inputs(styles, c))
        story.append(Spacer(1, 6))
        story.extend(_circuit_calcs(styles, c))
        story.append(Spacer(1, 6))
        story.extend(_circuit_reliability_score(styles, c))
        story.append(PageBreak())
        story.extend(_circuit_rules(styles, c))
        story.append(Spacer(1, 8))
        story.extend(_granite_section(styles, c.get("granite_explanation"), "5"))

    doc.build(
        story,
        onFirstPage=page_tpl.on_page,
        onLaterPages=page_tpl.on_page,
    )

    file_size = Path(output_path).stat().st_size
    logger.info(
        "PDF report generated: %s (%.1f KB) | session=%s | type=%s",
        Path(output_path).name, file_size / 1024, session_uid[:8], report_type,
    )
    return output_path
