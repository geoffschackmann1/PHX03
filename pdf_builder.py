"""
QPi Scorecard Automation — PDF Builder
Generates individual clinician scorecard PDFs (one per clinician).
Each PDF is a detailed 1-page report with all KPIs, visit breakdown,
documentation, cost, utilization, and optional prior-period trending.
"""
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, HRFlowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

from config import AGENCY, TARGETS, REPORT_VERSION
from scorecard_engine import evaluate_target

logger = logging.getLogger(__name__)

# =============================================================================
# COLOR PALETTE
# =============================================================================
NAVY = colors.HexColor("#1F4E79")
BLUE = colors.HexColor("#2E75B6")
LIGHT_BLUE = colors.HexColor("#D6E4F0")
GREEN = colors.HexColor("#006100")
GREEN_BG = colors.HexColor("#C6EFCE")
RED = colors.HexColor("#9C0006")
RED_BG = colors.HexColor("#FFC7CE")
AMBER = colors.HexColor("#9C6500")
AMBER_BG = colors.HexColor("#FFEB9C")
GRAY = colors.HexColor("#666666")
LIGHT_GRAY = colors.HexColor("#F2F2F2")
WHITE = colors.white


def _status_color(status: str) -> tuple:
    """Return (text_color, bg_color) for a status."""
    if status == "MEETS":
        return GREEN, GREEN_BG
    elif status in ("BELOW", "ABOVE"):
        return RED, RED_BG
    else:
        return GRAY, LIGHT_GRAY


def _fmt_pct(val) -> str:
    if val is None or pd.isna(val):
        return "—"
    return f"{val:.1f}%"


def _fmt_currency(val) -> str:
    if val is None or pd.isna(val) or val == 0:
        return "—"
    return f"${val:,.2f}"


def _fmt_num(val, decimals: int = 0) -> str:
    if val is None or pd.isna(val):
        return "—"
    if decimals == 0:
        return str(int(val))
    return f"{val:.{decimals}f}"


def _fmt_trend(val) -> str:
    """Format a trend value with arrow."""
    if val is None or pd.isna(val):
        return "—"
    if val > 0:
        return f"+{val:.1f}"
    elif val < 0:
        return f"{val:.1f}"
    return "0.0"


def _build_styles():
    """Create paragraph styles for the PDF."""
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        "Title_Custom", parent=styles["Title"],
        fontName="Helvetica-Bold", fontSize=14, textColor=NAVY,
        spaceAfter=1, alignment=TA_LEFT,
    ))
    styles.add(ParagraphStyle(
        "Subtitle", parent=styles["Normal"],
        fontName="Helvetica", fontSize=8, textColor=GRAY,
        spaceAfter=2, alignment=TA_LEFT,
    ))
    styles.add(ParagraphStyle(
        "SectionHead", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=10, textColor=NAVY,
        spaceBefore=4, spaceAfter=2, alignment=TA_LEFT,
    ))
    styles.add(ParagraphStyle(
        "BodyText_Custom", parent=styles["Normal"],
        fontName="Helvetica", fontSize=9, textColor=colors.black,
    ))
    styles.add(ParagraphStyle(
        "CellText", parent=styles["Normal"],
        fontName="Helvetica", fontSize=8, textColor=colors.black,
        alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        "CellBold", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=8, textColor=colors.black,
        alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        "FooterText", parent=styles["Normal"],
        fontName="Helvetica", fontSize=7, textColor=GRAY,
        alignment=TA_CENTER,
    ))

    return styles


def _status_cell(status: str) -> str:
    """Return styled status text for a table cell."""
    txt_color, _ = _status_color(status)
    hex_color = txt_color.hexval()[2:]  # strip '0x' prefix
    return f'<font color="#{hex_color}"><b>{status}</b></font>'


def _make_kpi_table(row: pd.Series, styles) -> Table:
    """Build the main KPI summary table."""
    meets = row.get("meets_target", False)
    prod_status = "MEETS" if meets else "BELOW"
    doc_status = evaluate_target(row.get("on_time_pct"), "doc_on_time_pct")
    cpv_status = evaluate_target(row.get("cpv"), "cpv")
    util_val = row.get("utilization_pct")
    util_status = evaluate_target(util_val, "utilization_pct") if pd.notna(util_val) else "N/A"

    data = [
        ["KPI", "Value", "Target", "Status"],
        [
            "Productivity (Points)",
            f"{row.get('points', 0):.1f} / {row.get('adj_target', 30):.1f}",
            "30 pts (adj)",
            prod_status,
        ],
        [
            "Productivity %",
            _fmt_pct(row.get("prod_pct")),
            "100%",
            prod_status,
        ],
        [
            "Documentation On-Time",
            _fmt_pct(row.get("on_time_pct")),
            "> 90%",
            doc_status,
        ],
        [
            "Cost Per Visit",
            _fmt_currency(row.get("cpv")),
            "< $105",
            cpv_status,
        ],
        [
            "Utilization",
            _fmt_pct(util_val),
            "100%",
            util_status,
        ],
        [
            "Points / Hour",
            _fmt_num(row.get("points_per_hour"), 2),
            "0.60",
            evaluate_target(row.get("points_per_hour"), "points_per_hour"),
        ],
    ]

    col_widths = [2.0 * inch, 1.6 * inch, 1.0 * inch, 0.8 * inch]
    t = Table(data, colWidths=col_widths)

    # Base style
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]

    # Color-code status column
    for i in range(1, len(data)):
        status = data[i][3]
        txt_color, bg_color = _status_color(status)
        style_cmds.append(("TEXTCOLOR", (3, i), (3, i), txt_color))
        style_cmds.append(("BACKGROUND", (3, i), (3, i), bg_color))
        style_cmds.append(("FONTNAME", (3, i), (3, i), "Helvetica-Bold"))

    t.setStyle(TableStyle(style_cmds))
    return t


def _make_visit_table(row: pd.Series) -> Table:
    """Build visit breakdown table."""
    data = [
        ["SOC/Eval", "ROC", "Recert", "Routine", "Discharge", "TOTAL"],
        [
            _fmt_num(row.get("soc_eval_ct", 0)),
            "0",  # ROC not yet split
            _fmt_num(row.get("recert_ct", 0)),
            _fmt_num(row.get("routine_ct", 0)),
            _fmt_num(row.get("discharge_ct", 0)),
            _fmt_num(row.get("total_visits", 0)),
        ],
    ]

    # Points row
    soc_pts = row.get("soc_eval_ct", 0) * 2.5
    rec_pts = row.get("recert_ct", 0) * 1.5
    rtn_pts = row.get("routine_ct", 0) * 1.0
    dc_pts = row.get("discharge_ct", 0) * 1.0
    total_pts = row.get("points", 0)
    data.append([
        f"{soc_pts:.1f} pts",
        "—",
        f"{rec_pts:.1f} pts",
        f"{rtn_pts:.1f} pts",
        f"{dc_pts:.1f} pts",
        f"{total_pts:.1f} pts",
    ])

    col_w = 0.9 * inch
    t = Table(data, colWidths=[col_w] * 6)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTNAME", (-1, 1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BACKGROUND", (-1, 1), (-1, -1), LIGHT_BLUE),
    ]))
    return t


def _make_doc_table(row: pd.Series) -> Table:
    """Build documentation detail table."""
    data = [
        ["Total Docs", "On Time", "Late", "On-Time %", "Avg Days", "Fastest", "Slowest"],
        [
            _fmt_num(row.get("total_docs", 0)),
            _fmt_num(row.get("docs_on_time", 0)),
            _fmt_num(row.get("docs_late", 0)),
            _fmt_pct(row.get("on_time_pct")),
            _fmt_num(row.get("avg_days_to_submit"), 1),
            _fmt_num(row.get("fastest_days")),
            _fmt_num(row.get("slowest_days")),
        ],
    ]

    col_w = [0.85 * inch, 0.75 * inch, 0.65 * inch, 0.85 * inch, 0.8 * inch, 0.75 * inch, 0.75 * inch]
    t = Table(data, colWidths=col_w)

    doc_status = evaluate_target(row.get("on_time_pct"), "doc_on_time_pct")
    txt_c, bg_c = _status_color(doc_status)

    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        # Color the on-time % cell
        ("TEXTCOLOR", (3, 1), (3, 1), txt_c),
        ("BACKGROUND", (3, 1), (3, 1), bg_c),
        ("FONTNAME", (3, 1), (3, 1), "Helvetica-Bold"),
    ]))
    return t


def _make_cost_table(row: pd.Series) -> Table:
    """Build cost breakdown table."""
    data = [
        ["Gross Wages", "Mileage", "Total Cost", "Visits", "CPV", "vs $105"],
        [
            _fmt_currency(row.get("gross_wages")),
            _fmt_currency(row.get("mileage")),
            _fmt_currency(row.get("total_cost")),
            _fmt_num(row.get("total_visits", 0)),
            _fmt_currency(row.get("cpv")),
            "",
        ],
    ]

    # Calculate variance
    cpv = row.get("cpv")
    if cpv is not None and not pd.isna(cpv) and cpv > 0:
        var = cpv - 105
        data[1][5] = f"{'+'if var >= 0 else ''}{var:.2f}"

    col_w = [1.1 * inch, 0.9 * inch, 1.1 * inch, 0.7 * inch, 0.9 * inch, 0.7 * inch]
    t = Table(data, colWidths=col_w)

    cpv_status = evaluate_target(cpv, "cpv")
    txt_c, bg_c = _status_color(cpv_status)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        # Color CPV cell
        ("TEXTCOLOR", (4, 1), (4, 1), txt_c),
        ("BACKGROUND", (4, 1), (4, 1), bg_c),
        ("FONTNAME", (4, 1), (4, 1), "Helvetica-Bold"),
    ]

    # Color variance cell
    if cpv is not None and not pd.isna(cpv) and cpv > 0:
        var_color = GREEN if cpv < 105 else RED
        style_cmds.append(("TEXTCOLOR", (5, 1), (5, 1), var_color))
        style_cmds.append(("FONTNAME", (5, 1), (5, 1), "Helvetica-Bold"))

    t.setStyle(TableStyle(style_cmds))
    return t


def _make_utilization_table(row: pd.Series) -> Table:
    """Build utilization / hours table."""
    data = [
        ["BP Hours", "Actual Hours", "Utilization %", "OT Hours", "Time Off", "Pts/Hr"],
        [
            _fmt_num(row.get("bp_hours"), 1),
            _fmt_num(row.get("regular_hours"), 1) if pd.notna(row.get("regular_hours")) and row.get("regular_hours", 0) > 0 else "—",
            _fmt_pct(row.get("utilization_pct")),
            _fmt_num(row.get("overtime_hours"), 1),
            _fmt_num(row.get("time_off_hours"), 1),
            _fmt_num(row.get("points_per_hour"), 2),
        ],
    ]

    col_w = [0.9 * inch] * 6
    t = Table(data, colWidths=col_w)

    util_status = evaluate_target(row.get("utilization_pct"), "utilization_pct")
    txt_c, bg_c = _status_color(util_status)

    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        # Color utilization cell
        ("TEXTCOLOR", (2, 1), (2, 1), txt_c),
        ("BACKGROUND", (2, 1), (2, 1), bg_c),
        ("FONTNAME", (2, 1), (2, 1), "Helvetica-Bold"),
    ]))
    return t


def _make_trend_table(row: pd.Series) -> Optional[Table]:
    """Build prior period trending table (returns None if no trend data)."""
    has_trend = (
        pd.notna(row.get("prior_points"))
        or pd.notna(row.get("prior_visits"))
        or pd.notna(row.get("prior_on_time_pct"))
    )
    if not has_trend:
        return None

    data = [
        ["Metric", "Current", "Prior", "Change"],
        [
            "Points",
            _fmt_num(row.get("points"), 1),
            _fmt_num(row.get("prior_points"), 1),
            _fmt_trend(row.get("points_trend")),
        ],
        [
            "Visits",
            _fmt_num(row.get("total_visits")),
            _fmt_num(row.get("prior_visits")),
            _fmt_trend(row.get("visits_trend")),
        ],
        [
            "Doc On-Time %",
            _fmt_pct(row.get("on_time_pct")),
            _fmt_pct(row.get("prior_on_time_pct")),
            _fmt_trend(row.get("doc_trend")),
        ],
    ]

    col_w = [1.4 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch]
    t = Table(data, colWidths=col_w)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 1), (-1, -1), "Helvetica"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]

    # Color the change column based on positive/negative
    for i in range(1, len(data)):
        trend_val = None
        if i == 1:
            trend_val = row.get("points_trend")
        elif i == 2:
            trend_val = row.get("visits_trend")
        elif i == 3:
            trend_val = row.get("doc_trend")

        if trend_val is not None and not pd.isna(trend_val):
            color = GREEN if trend_val > 0 else RED if trend_val < 0 else GRAY
            style_cmds.append(("TEXTCOLOR", (3, i), (3, i), color))
            style_cmds.append(("FONTNAME", (3, i), (3, i), "Helvetica-Bold"))

    t.setStyle(TableStyle(style_cmds))
    return t


def _make_status_banner(row: pd.Series) -> Table:
    """Build overall status banner at the top."""
    meets = row.get("meets_target", False)
    opp = row.get("opp_eligible", False)

    if opp:
        label = "OPP ELIGIBLE — MEETS ALL TARGETS"
        bg = GREEN_BG
        txt = GREEN
    elif meets:
        label = "MEETS PRODUCTIVITY TARGET"
        bg = GREEN_BG
        txt = GREEN
    else:
        label = "BELOW PRODUCTIVITY TARGET"
        bg = RED_BG
        txt = RED

    data = [[label]]
    t = Table(data, colWidths=[5.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("TEXTCOLOR", (0, 0), (-1, -1), txt),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BOX", (0, 0), (-1, -1), 1, txt),
    ]))
    return t


def build_clinician_pdf(
    row: pd.Series,
    pay_period: dict,
    output_path: Path,
) -> Path:
    """
    Build a single clinician's PDF scorecard.

    Args:
        row: One row from the clinician scorecard DataFrame.
        pay_period: Pay period dict with label, range_str, etc.
        output_path: Where to write the PDF.

    Returns:
        Path to the generated PDF.
    """
    styles = _build_styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        topMargin=0.4 * inch,
        bottomMargin=0.4 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )

    elements = []

    # -- Header --
    clinician_name = row.get("clinician_name", "Unknown")
    discipline = row.get("discipline", "")
    skill_level = row.get("skill_level", "")
    emp_type = row.get("emp_type", "")

    elements.append(Paragraph(
        f"{AGENCY.name} — QPi Clinician Scorecard",
        styles["Title_Custom"],
    ))
    elements.append(Paragraph(
        f"{pay_period['label']}  |  {pay_period['range_str']}",
        styles["Subtitle"],
    ))
    generated = datetime.now().strftime("%m/%d/%Y %I:%M %p")
    elements.append(Paragraph(
        f"Report v{REPORT_VERSION}  |  Generated: {generated}",
        styles["Subtitle"],
    ))
    elements.append(Spacer(1, 2))

    # -- Clinician Info Bar --
    info_data = [[
        f"{clinician_name}",
        f"{discipline}",
        f"{emp_type}",
        f"{skill_level}",
    ]]
    info_headers = [["Clinician", "Discipline", "Employment", "Skill Level"]]
    info_t = Table(info_headers + info_data, colWidths=[2.2 * inch, 1.2 * inch, 1.2 * inch, 1.2 * inch])
    info_t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(info_t)
    elements.append(Spacer(1, 4))

    # -- Status Banner --
    elements.append(_make_status_banner(row))
    elements.append(Spacer(1, 4))

    # -- KPI Summary --
    elements.append(Paragraph("KPI Summary", styles["SectionHead"]))
    elements.append(_make_kpi_table(row, styles))
    elements.append(Spacer(1, 2))

    # -- Visit Breakdown --
    elements.append(Paragraph("Visit Breakdown", styles["SectionHead"]))
    elements.append(_make_visit_table(row))

    # Patient info line
    patients = _fmt_num(row.get("unique_patients"))
    vpp = _fmt_num(row.get("visits_per_patient"), 1)
    elements.append(Paragraph(
        f"Unique Patients: {patients}  |  Visits/Patient: {vpp}  |  "
        f"SOC Mix: {_fmt_pct(row.get('pct_soc_eval'))}  |  "
        f"Routine Mix: {_fmt_pct(row.get('pct_routine'))}",
        styles["Subtitle"],
    ))
    elements.append(Spacer(1, 2))

    # -- Documentation --
    elements.append(Paragraph("Documentation Timeliness", styles["SectionHead"]))
    elements.append(_make_doc_table(row))
    elements.append(Spacer(1, 2))

    # -- Utilization & Hours --
    elements.append(Paragraph("Utilization & Hours", styles["SectionHead"]))
    elements.append(_make_utilization_table(row))
    elements.append(Spacer(1, 2))

    # -- Cost Per Visit --
    elements.append(Paragraph("Cost Per Visit", styles["SectionHead"]))
    elements.append(_make_cost_table(row))
    elements.append(Spacer(1, 2))

    # -- Trending (if available) --
    trend_table = _make_trend_table(row)
    if trend_table is not None:
        elements.append(Paragraph("Prior Period Trend", styles["SectionHead"]))
        elements.append(trend_table)
        elements.append(Spacer(1, 2))

    # -- Footer --
    elements.append(HRFlowable(width="100%", thickness=0.5, color=GRAY))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        f"Confidential — {AGENCY.name} — QPi Scorecard v{REPORT_VERSION}",
        styles["FooterText"],
    ))

    doc.build(elements)
    return output_path


def build_all_clinician_pdfs(
    clinician_df: pd.DataFrame,
    pay_period: dict,
    output_dir: Path,
) -> list[Path]:
    """
    Generate one PDF per clinician.

    Args:
        clinician_df: Full scorecard DataFrame from scorecard_engine.
        pay_period: Pay period dict.
        output_dir: Base directory. PDFs go into output_dir/clinician_pdfs/<PP_label>/

    Returns:
        List of paths to generated PDFs.
    """
    pdf_dir = output_dir / "clinician_pdfs" / pay_period["label"]
    pdf_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for _, row in clinician_df.iterrows():
        name = row.get("clinician_name", "Unknown")
        safe_name = name.replace(" ", "_").replace("/", "-")
        filename = f"QPi_{safe_name}_{pay_period['label']}.pdf"
        pdf_path = pdf_dir / filename

        try:
            build_clinician_pdf(row, pay_period, pdf_path)
            paths.append(pdf_path)
        except Exception as e:
            logger.error(f"Failed to generate PDF for {name}: {e}")

    logger.info(f"Generated {len(paths)} clinician PDFs in {pdf_dir}")
    return paths
