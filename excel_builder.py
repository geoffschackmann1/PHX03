"""
QPi Scorecard Automation — Excel Builder
Generates the multi-tab scorecard workbook and individual clinician PDFs.
"""
import logging
from pathlib import Path
from datetime import date

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from config import TARGETS, AGENCIES
from scorecard_engine import AgencyMetrics, evaluate_target

logger = logging.getLogger(__name__)

# =============================================================================
# STYLE DEFINITIONS
# =============================================================================
HF = Font(name='Arial', bold=True, size=10, color='FFFFFF')
HFILL = PatternFill('solid', fgColor='1F4E79')
TF = Font(name='Arial', bold=True, size=14, color='1F4E79')
SF = Font(name='Arial', bold=True, size=12, color='1F4E79')
BF = Font(name='Arial', bold=True, size=10)
DF = Font(name='Arial', size=10)
GF = Font(name='Arial', bold=True, size=10, color='006100')
RF = Font(name='Arial', bold=True, size=10, color='9C0006')
AF = Font(name='Arial', bold=True, size=10, color='9C6500')
GFL = PatternFill('solid', fgColor='C6EFCE')
RFL = PatternFill('solid', fgColor='FFC7CE')
AFL = PatternFill('solid', fgColor='FFEB9C')
TOTFL = PatternFill('solid', fgColor='D9E2F3')
SECFL = PatternFill('solid', fgColor='D6E4F0')
TB = Border(left=Side('thin'), right=Side('thin'), top=Side('thin'), bottom=Side('thin'))
CA = Alignment(horizontal='center', vertical='center', wrap_text=True)
LA = Alignment(horizontal='left', vertical='center', wrap_text=True)


def _header_row(ws, row, num_cols):
    for c in range(1, num_cols + 1):
        cl = ws.cell(row=row, column=c)
        cl.font = HF; cl.fill = HFILL; cl.alignment = CA; cl.border = TB


def _cell(ws, r, c, val=None, fmt=None):
    cl = ws.cell(row=r, column=c)
    if val is not None:
        cl.value = val
    cl.font = DF; cl.alignment = CA; cl.border = TB
    if fmt:
        cl.number_format = fmt
    return cl


def _color_status(cell, status):
    if status == "MEETS":
        cell.font = GF; cell.fill = GFL
    elif status in ("BELOW", "ABOVE"):
        cell.font = RF; cell.fill = RFL
    elif status == "WARN":
        cell.font = AF; cell.fill = AFL


def _color_value(cell, val, target_key):
    status = evaluate_target(val, target_key)
    _color_status(cell, status)


def build_workbook(
    clinician_df: pd.DataFrame,
    agency_metrics: AgencyMetrics,
    pay_period: dict,
    clinic_key: int,
    output_path: Path,
):
    """
    Build the full QPi scorecard Excel workbook.
    Tabs: Clinician Scorecard, Efficiency Analysis, Cost Per Visit,
          Documentation Detail, Agency Roll-Up, Executive Summary
    """
    agency = AGENCIES.get(clinic_key)
    agency_name = agency.name if agency else f"Agency {clinic_key}"
    pp_label = pay_period["label"]
    pp_range = pay_period["range_str"]

    wb = openpyxl.Workbook()

    # ==================================================================
    # TAB 1: CLINICIAN SCORECARD
    # ==================================================================
    ws1 = wb.active
    ws1.title = "Clinician Scorecard"
    ws1.sheet_properties.tabColor = "1F4E79"

    ws1["A1"] = f"{agency_name} — QPi Clinician Scorecard ({pp_label})"
    ws1["A1"].font = TF
    ws1.merge_cells("A1:AB1")
    ws1["A2"] = f"{pp_range}  |  Source: Snowflake + iSolved"
    ws1["A2"].font = Font(name='Arial', size=9, italic=True, color='666666')

    heads = [
        "Clinician", "Disc", "Type", "Skill",
        "SOC", "ROC", "Rec", "Rtn", "DC", "Visits",
        "Points", "Time Off", "Adj Tgt", "Prod %", "Meets",
        "BP Hrs", "Act Hrs", "Util %", "Pts/Hr",
        "Patients", "Vis/Pt",
        "% SOC", "% Routine",
        "OT %", "Accuracy %", "Missed %",
        "Total Cost", "CPV",
    ]
    hr = 4
    for ci, h in enumerate(heads, 1):
        ws1.cell(row=hr, column=ci, value=h)
    _header_row(ws1, hr, len(heads))

    for ri, (_, row) in enumerate(clinician_df.iterrows()):
        r = hr + 1 + ri
        vals = [
            row.get("clinician_name", ""),
            row.get("discipline", ""),
            row.get("emp_type", ""),
            row.get("skill_level", ""),
            row.get("soc_eval_ct", 0),
            0,  # ROC (not yet split)
            row.get("recert_ct", 0),
            row.get("routine_ct", 0),
            row.get("discharge_ct", 0),
            row.get("total_visits", 0),
            row.get("points", 0),
            row.get("time_off_hours", 0),
            row.get("adj_target", 30),
            row.get("prod_pct", 0),
            "YES" if row.get("meets_target", False) else "NO",
            round(row.get("bp_hours", 0), 1),
            row.get("regular_hours", "") if pd.notna(row.get("regular_hours")) else "",
            row.get("utilization_pct", "") if pd.notna(row.get("utilization_pct")) else "",
            row.get("points_per_hour", "") if pd.notna(row.get("points_per_hour")) else "",
            row.get("unique_patients", 0),
            row.get("visits_per_patient", 0),
            row.get("pct_soc_eval", 0),
            row.get("pct_routine", 0),
            row.get("on_time_pct", 0),
            100.0,  # Accuracy (placeholder)
            row.get("missed_visit_pct", 0),
            row.get("total_cost", "") if pd.notna(row.get("total_cost")) and row.get("total_cost", 0) > 0 else "",
            row.get("cpv", "") if pd.notna(row.get("cpv")) else "",
        ]
        for ci, v in enumerate(vals, 1):
            _cell(ws1, r, ci, v)

        # Color coding
        meets_cell = ws1.cell(row=r, column=15)
        if row.get("meets_target", False):
            meets_cell.fill = GFL; meets_cell.font = GF
        else:
            meets_cell.fill = RFL; meets_cell.font = RF

        if pd.notna(row.get("on_time_pct")):
            _color_value(ws1.cell(row=r, column=24), row["on_time_pct"], "doc_on_time_pct")

        if pd.notna(row.get("cpv")) and row.get("cpv", 0) > 0:
            ws1.cell(row=r, column=27).number_format = "$#,##0.00"
            cpv_cell = ws1.cell(row=r, column=28)
            cpv_cell.number_format = "$#,##0.00"
            _color_value(cpv_cell, row["cpv"], "cpv")

    # Column widths
    widths = [22, 6, 8, 10, 6, 6, 6, 6, 6, 7, 8, 9, 8, 8, 7, 8, 8, 8, 7, 9, 8, 8, 9, 10, 10, 9, 12, 10]
    for i, w in enumerate(widths, 1):
        ws1.column_dimensions[get_column_letter(i)].width = w
    ws1.freeze_panes = "A5"

    # ==================================================================
    # TAB 2: AGENCY ROLL-UP
    # ==================================================================
    ws2 = wb.create_sheet("Agency Roll-Up")
    ws2.sheet_properties.tabColor = "2E75B6"

    ws2["B2"] = f"{agency_name} — Agency QPi Roll-Up ({pp_label})"
    ws2["B2"].font = TF

    def _agency_row(ws, r, kpi, val, tgt, status):
        ws.cell(row=r, column=2, value=kpi).font = BF
        ws.cell(row=r, column=2).border = TB
        _cell(ws, r, 3, val)
        _cell(ws, r, 4, tgt)
        st_cell = _cell(ws, r, 5, status)
        _color_status(st_cell, status)

    # Section 1: Quality & Productivity
    ws2["B5"] = "SECTION 1: QUALITY & PRODUCTIVITY"
    ws2["B5"].font = SF
    for ci, h in enumerate(["KPI", "Value", "Target", "Status"], 2):
        ws2.cell(row=6, column=ci, value=h)
    _header_row(ws2, 6, 5)

    n = len(clinician_df)
    meets_ct = int(clinician_df["meets_target"].sum())
    avg_ot = clinician_df["on_time_pct"].mean() if not clinician_df.empty else 0
    pph_vals = clinician_df["points_per_hour"].dropna()
    avg_pph = round(pph_vals.mean(), 2) if not pph_vals.empty else 0

    _agency_row(ws2, 7, "Star Rating (SHP)", "Pending", "≥ 4 Stars", "N/A")
    _agency_row(ws2, 8, "Avg NPS Score", "Pending", "> 90%", "N/A")
    _agency_row(ws2, 9, "Avg Doc On-Time %", f"{avg_ot:.1f}%", "> 90%",
                evaluate_target(avg_ot, "doc_on_time_pct"))
    _agency_row(ws2, 10, "Clinicians Meeting Adj Target", f"{meets_ct}/{n}",
                "100%", "BELOW" if meets_ct < n else "MEETS")
    _agency_row(ws2, 11, "Avg Points/Hour", str(avg_pph), "≥ 0.60",
                evaluate_target(avg_pph, "points_per_hour"))

    # Section 2: Operations
    ws2["B13"] = "SECTION 2: OPERATIONS & CLINICAL"
    ws2["B13"].font = SF
    for ci, h in enumerate(["KPI", "Value", "Target", "Status"], 2):
        ws2.cell(row=14, column=ci, value=h)
    _header_row(ws2, 14, 5)

    _agency_row(ws2, 15, "Census", str(agency_metrics.census), "> 125",
                evaluate_target(agency_metrics.census, "census"))
    _agency_row(ws2, 16, "Visits/Episode", str(agency_metrics.visits_per_episode), "≤ 8.5",
                evaluate_target(agency_metrics.visits_per_episode, "visits_per_episode"))
    _agency_row(ws2, 17, "LUPA %", f"{agency_metrics.lupa_pct}%", "≤ 7%",
                evaluate_target(agency_metrics.lupa_pct, "lupa_pct"))
    _agency_row(ws2, 18, "Non-Admits", str(agency_metrics.non_admits), "≤ 3",
                evaluate_target(agency_metrics.non_admits, "non_admits"))
    _agency_row(ws2, 19, "Assistant Utilization", f"{agency_metrics.assistant_pct}%", "> 40%",
                evaluate_target(agency_metrics.assistant_pct, "assistant_pct"))

    # Section 3: Revenue & Cost
    ws2["B21"] = "SECTION 3: REVENUE & COST"
    ws2["B21"].font = SF
    for ci, h in enumerate(["KPI", "Value", "Target", "Status"], 2):
        ws2.cell(row=22, column=ci, value=h)
    _header_row(ws2, 22, 5)

    cpv_vals = clinician_df["cpv"].dropna()
    avg_cpv = round(cpv_vals.mean(), 2) if not cpv_vals.empty else 0

    _agency_row(ws2, 23, "Agency Avg CPV", f"${avg_cpv:.2f}", "< $105",
                evaluate_target(avg_cpv, "cpv"))
    _agency_row(ws2, 24, "SoC Medicare %",
                f"{agency_metrics.soc_medicare_pct}% ({agency_metrics.soc_medicare_ct}/{agency_metrics.soc_total})",
                "> 80%", evaluate_target(agency_metrics.soc_medicare_pct, "soc_medicare_pct"))

    ws2.column_dimensions["B"].width = 35
    ws2.column_dimensions["C"].width = 30
    ws2.column_dimensions["D"].width = 15
    ws2.column_dimensions["E"].width = 18

    # ==================================================================
    # TAB 3: DOCUMENTATION DETAIL
    # ==================================================================
    ws3 = wb.create_sheet("Documentation Detail")
    ws3.sheet_properties.tabColor = "70AD47"
    ws3["A1"] = f"Documentation Timeliness — {pp_label} ({pp_range})"
    ws3["A1"].font = TF

    doc_heads = ["Clinician", "Disc", "Total Docs", "On Time", "Late",
                 "On Time %", "Status", "Avg Days", "Fastest", "Slowest"]
    for ci, h in enumerate(doc_heads, 1):
        ws3.cell(row=3, column=ci, value=h)
    _header_row(ws3, 3, len(doc_heads))

    doc_sorted = clinician_df.sort_values("on_time_pct", ascending=True)
    for ri, (_, row) in enumerate(doc_sorted.iterrows()):
        r = 4 + ri
        _cell(ws3, r, 1, row.get("clinician_name", ""))
        _cell(ws3, r, 2, row.get("discipline", ""))
        _cell(ws3, r, 3, int(row.get("total_docs", 0)))
        _cell(ws3, r, 4, int(row.get("docs_on_time", 0)))
        _cell(ws3, r, 5, int(row.get("docs_late", 0)))
        ot_cell = _cell(ws3, r, 6, row.get("on_time_pct", 0))
        _color_value(ot_cell, row.get("on_time_pct", 0), "doc_on_time_pct")
        status = evaluate_target(row.get("on_time_pct", 0), "doc_on_time_pct")
        st_cell = _cell(ws3, r, 7, "MEETS TARGET" if status == "MEETS" else "BELOW TARGET")
        _color_status(st_cell, status)
        _cell(ws3, r, 8, row.get("avg_days_to_submit", ""))
        _cell(ws3, r, 9, row.get("fastest_days", ""))
        _cell(ws3, r, 10, row.get("slowest_days", ""))

    doc_widths = [22, 8, 10, 10, 8, 11, 16, 10, 10, 10]
    for i, w in enumerate(doc_widths, 1):
        ws3.column_dimensions[get_column_letter(i)].width = w

    # ==================================================================
    # TAB 4: COST PER VISIT
    # ==================================================================
    ws4 = wb.create_sheet("Cost Per Visit")
    ws4.sheet_properties.tabColor = "ED7D31"
    ws4["A1"] = f"Cost Per Visit Analysis — {pp_label} ({pp_range})"
    ws4["A1"].font = TF

    cpv_heads = ["Clinician", "Disc", "Type", "Gross Wage", "Mileage",
                 "Total Cost", "Visits", "CPV", "vs $105"]
    for ci, h in enumerate(cpv_heads, 1):
        ws4.cell(row=3, column=ci, value=h)
    _header_row(ws4, 3, len(cpv_heads))

    cpv_df = clinician_df[clinician_df["cpv"].notna() & (clinician_df["cpv"] > 0)].sort_values("cpv")
    for ri, (_, row) in enumerate(cpv_df.iterrows()):
        r = 4 + ri
        _cell(ws4, r, 1, row["clinician_name"])
        _cell(ws4, r, 2, row["discipline"])
        _cell(ws4, r, 3, row.get("emp_type", ""))
        _cell(ws4, r, 4, row["gross_wages"]).number_format = "$#,##0.00"
        _cell(ws4, r, 5, row["mileage"]).number_format = "$#,##0.00"
        _cell(ws4, r, 6, row["total_cost"]).number_format = "$#,##0.00"
        _cell(ws4, r, 7, int(row["total_visits"]))
        cpv_cell = _cell(ws4, r, 8, row["cpv"])
        cpv_cell.number_format = "$#,##0.00"
        _color_value(cpv_cell, row["cpv"], "cpv")
        var = round(row["cpv"] - 105, 2)
        vc = _cell(ws4, r, 9, var)
        vc.number_format = "$#,##0.00;($#,##0.00)"
        if var <= 0:
            vc.font = GF
        else:
            vc.font = RF

    cpv_widths = [22, 8, 12, 13, 12, 13, 8, 12, 12]
    for i, w in enumerate(cpv_widths, 1):
        ws4.column_dimensions[get_column_letter(i)].width = w

    # ==================================================================
    # SAVE
    # ==================================================================
    wb.save(str(output_path))
    logger.info(f"Workbook saved: {output_path}")
    return output_path
