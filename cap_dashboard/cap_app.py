"""
Cap Dashboard — Streamlit Application
American Premier Hospice — Medicare Hospice Cap Tracking

Run locally:  streamlit run cap_dashboard/cap_app.py
"""
import sys
import os
import logging
from datetime import date

# Add parent directory for QPi imports — MUST be before any config imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load credentials: Streamlit Cloud uses st.secrets, local dev uses .env
import streamlit as st

_SECRETS_KEYS = ["SF_ACCOUNT", "SF_USER", "SF_PASSWORD", "SF_ROLE", "SF_WAREHOUSE"]
try:
    if "SF_ACCOUNT" in st.secrets:
        for _k in _SECRETS_KEYS:
            if _k in st.secrets:
                os.environ[_k] = str(st.secrets[_k])
    else:
        from load_env import load_dotenv
        load_dotenv()
except Exception:
    from load_env import load_dotenv
    load_dotenv()

import pandas as pd

from cap_config import (
    AGENCY_NAME,
    CAP_YEARS,
    DEFAULT_CAP_YEAR,
    RISK_TIERS,
    CAP_ALERT_THRESHOLD,
    CAP_DANGER_THRESHOLD,
    CURRENCY_FORMAT,
)
from cap_snowflake_client import CapSnowflakeClient
from cap_engine import (
    process_cap_patients,
    merge_payments_to_patients,
    build_cap_summary,
    calculate_monthly_payments,
    calculate_cumulative_position,
)
from cap_report import build_cap_workbook

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title=f"{AGENCY_NAME} — Cap Dashboard",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =============================================================================
# DATA LOADING (cached)
# =============================================================================
@st.cache_data(ttl=600, show_spinner="Loading data from Snowflake...")
def load_cap_data(cap_year_key: str) -> dict:
    """Load all cap data from Snowflake for the selected cap year."""
    params = CAP_YEARS[cap_year_key]
    cap_start = params["start"]
    cap_end = params["end"]

    client = CapSnowflakeClient()
    try:
        client.connect()
        data = client.get_all_cap_data(cap_start, cap_end)
    finally:
        client.close()

    return {
        "patients": data["patients"],
        "benefit_periods": data["benefit_periods"],
        "payments": data["payments"],
        "census": data["census"],
    }


def process_cap_data(raw: dict, cap_year_key: str) -> dict:
    """Run all calculations on raw Snowflake data."""
    patients = process_cap_patients(raw["patients"])
    patients = merge_payments_to_patients(patients, raw["payments"])
    summary = build_cap_summary(patients, cap_year_key)
    monthly = calculate_monthly_payments(raw["payments"])
    if not monthly.empty and summary["est_bene_count"] > 0:
        monthly = calculate_cumulative_position(
            monthly, summary["cap_rate"], summary["est_bene_count"]
        )

    return {
        "patients": patients,
        "summary": summary,
        "monthly": monthly,
        "payments": raw["payments"],
    }


# =============================================================================
# SIDEBAR
# =============================================================================
def render_sidebar() -> str:
    """Render sidebar controls and return selected cap year."""
    st.sidebar.title("Cap Dashboard")
    st.sidebar.markdown(f"**{AGENCY_NAME}**")
    st.sidebar.divider()

    cap_year_key = st.sidebar.selectbox(
        "Cap Year",
        options=list(CAP_YEARS.keys()),
        index=list(CAP_YEARS.keys()).index(DEFAULT_CAP_YEAR),
        format_func=lambda k: CAP_YEARS[k]["label"],
    )

    st.sidebar.divider()
    st.sidebar.markdown("**Cap Rates**")
    for key, cy in CAP_YEARS.items():
        st.sidebar.text(f"{key}: ${cy['cap_rate']:,.2f}")

    st.sidebar.divider()
    if st.sidebar.button("Clear Cache & Reload"):
        st.cache_data.clear()
        st.rerun()

    return cap_year_key


# =============================================================================
# MAIN PAGES
# =============================================================================

def render_overview(summary: dict, monthly: pd.DataFrame):
    """Page 1: Cap Position Overview — PS&R is primary when available."""
    st.header("Cap Position Overview")

    has_psr = summary.get("auth_bene_count") is not None
    alert = summary["alert_status"]

    # --- Alert Banner (driven by PS&R when available) ---
    if alert == "DANGER":
        if has_psr and summary.get("auth_position", 0) < 0:
            over_amt = abs(summary["auth_position"])
            st.error(
                f"OVER CAP by ${over_amt:,.2f} — "
                f"PS&R utilization at {summary.get('auth_utilization_pct', '?')}%. "
                f"Immediate action required."
            )
        else:
            pct = summary.get("auth_utilization_pct") or summary.get("est_utilization_pct", "?")
            st.error(f"CAP ALERT: Utilization at {pct}% — approaching cap limit!")
    elif alert == "WARNING":
        pct = summary.get("auth_utilization_pct") or summary.get("est_utilization_pct", "?")
        st.warning(f"CAP WARNING: Utilization at {pct}% — monitor closely.")
    else:
        st.success("Cap position is healthy.")

    # --- Key Metrics Row (PS&R primary when available) ---
    if has_psr:
        # PS&R is authoritative — show those numbers first
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric(
                "PS&R Pro-Rated Bene",
                f"{summary['auth_bene_count']:.4f}",
            )
        with col2:
            st.metric(
                "PS&R Cap Allowable",
                f"${summary['auth_allowable']:,.2f}",
            )
        with col3:
            st.metric(
                "PS&R Net Reimbursement",
                f"${summary['auth_net_pymts']:,.2f}",
            )
        with col4:
            pos = summary["auth_position"]
            st.metric(
                "PS&R Cap Position",
                f"${pos:,.2f}",
                delta=f"{'Under' if pos >= 0 else 'OVER'} Cap",
                delta_color="normal" if pos >= 0 else "inverse",
            )
    else:
        # No PS&R — fall back to Kinnser estimate
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Est. Pro-Rated Bene", f"{summary['est_bene_count']:.4f}")
        with col2:
            st.metric("Est. Cap Allowable", f"${summary['est_allowable']:,.2f}")
        with col3:
            val = summary.get("est_net_pymts")
            st.metric("Est. Net Reimbursement", f"${val:,.2f}" if val is not None else "N/A")
        with col4:
            pos = summary.get("est_position")
            st.metric(
                "Est. Cap Position",
                f"${pos:,.2f}" if pos is not None else "N/A",
                delta=f"{'Under' if pos and pos >= 0 else 'OVER'} Cap" if pos is not None else None,
                delta_color="normal" if pos and pos >= 0 else "inverse",
            )

    st.divider()

    # --- PS&R Authoritative (primary, shown first) ---
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("PS&R Authoritative (CMS Filing)")
        if has_psr:
            psr_data = {
                "Metric": [
                    "Pro-Rated Beneficiaries",
                    "Cap Rate (per bene)",
                    "Cap Allowable",
                    "Gross Reimbursement",
                    "Sequestration (2%)",
                    "Net Reimbursement",
                    "Cap Position",
                    "Utilization %",
                ],
                "Value": [
                    f"{summary['auth_bene_count']:.4f}",
                    f"${summary['cap_rate']:,.2f}",
                    f"${summary['auth_allowable']:,.2f}",
                    f"${summary['auth_gross_pymts']:,.2f}",
                    f"${summary['auth_sequestration']:,.2f}",
                    f"${summary['auth_net_pymts']:,.2f}",
                    f"${summary['auth_position']:,.2f}",
                    f"{summary['auth_utilization_pct']:.1f}%",
                ],
            }
            st.dataframe(pd.DataFrame(psr_data), use_container_width=True, hide_index=True)
            if summary["auth_position"] < 0:
                st.error(f"Agency is OVER the aggregate cap by ${abs(summary['auth_position']):,.2f}")
        else:
            st.info("PS&R actuals not yet filed for this cap year.")

    with col_b:
        st.subheader("WellSky/Kinnser Estimate")
        st.caption("Internal estimate from Snowflake data — supplementary only")
        est_data = {
            "Metric": [
                "Pro-Rated Bene (est.)",
                "Cap Allowable (est.)",
                "Net Reimbursement (est.)",
                "Cap Position (est.)",
                "Utilization % (est.)",
            ],
            "Value": [
                f"{summary['est_bene_count']:.4f}",
                f"${summary['est_allowable']:,.2f}",
                f"${summary['est_net_pymts']:,.2f}" if summary.get("est_net_pymts") is not None else "N/A",
                f"${summary['est_position']:,.2f}" if summary.get("est_position") is not None else "N/A",
                f"{summary['est_utilization_pct']:.1f}%" if summary.get("est_utilization_pct") is not None else "N/A",
            ],
        }
        st.dataframe(pd.DataFrame(est_data), use_container_width=True, hide_index=True)

    # --- Monthly Trend Chart ---
    if not monthly.empty and "cumulative_reimbursement" in monthly.columns:
        st.divider()
        st.subheader("Cumulative Reimbursement vs. Cap Allowable")
        chart_data = monthly[["month", "cumulative_reimbursement", "cap_allowable"]].copy()
        chart_data = chart_data.set_index("month")
        chart_data.columns = ["Cumulative Reimbursement", "Cap Allowable"]
        st.line_chart(chart_data)

        st.subheader("Monthly Net Reimbursement")
        bar_data = monthly[["month", "total_net_reimbursement"]].copy()
        bar_data = bar_data.set_index("month")
        bar_data.columns = ["Net Reimbursement"]
        st.bar_chart(bar_data)


def render_patient_detail(patients_df: pd.DataFrame):
    """Page 2: Patient-Level Detail."""
    st.header("Patient Detail — Cap Credits & Risk")

    if patients_df.empty:
        st.warning("No patient data available.")
        return

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        tier_filter = st.multiselect(
            "Risk Tier",
            options=["HIGH", "WATCH", "HEALTHY"],
            default=["HIGH", "WATCH", "HEALTHY"],
        )
    with col2:
        active_filter = st.selectbox("Status", ["All", "Active Only", "Discharged Only"])
    with col3:
        inherited_filter = st.selectbox("Inherited", ["All", "Inherited Only", "Post-Acquisition Only"])

    df = patients_df.copy()
    df = df[df["risk_tier"].isin(tier_filter)]
    if active_filter == "Active Only":
        df = df[df["is_active"]]
    elif active_filter == "Discharged Only":
        df = df[~df["is_active"]]
    if inherited_filter == "Inherited Only":
        df = df[df["is_inherited"]]
    elif inherited_filter == "Post-Acquisition Only":
        df = df[~df["is_inherited"]]

    # Summary metrics for filtered set
    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        st.metric("Patients", len(df))
    with col_b:
        st.metric("Total Cap Credit", f"{df['cap_credit'].sum():.4f}")
    with col_c:
        st.metric("High Risk", int((df["risk_tier"] == "HIGH").sum()))
    with col_d:
        if "total_net_reimbursement" in df.columns:
            st.metric("Total Reimb.", f"${df['total_net_reimbursement'].sum():,.2f}")

    # Data table
    display_cols = [
        "last_name", "first_name", "medical_record_number", "medicare_number",
        "start_of_care_date", "discharge_date", "max_bp", "risk_tier",
        "total_loc_days", "cap_credit",
    ]
    if "total_net_reimbursement" in df.columns:
        display_cols.append("total_net_reimbursement")
    display_cols.extend(["is_active", "is_inherited"])

    available = [c for c in display_cols if c in df.columns]
    display_df = df[available].copy()

    # Format dates for display
    for col in ["start_of_care_date", "discharge_date"]:
        if col in display_df.columns:
            display_df[col] = pd.to_datetime(display_df[col], errors="coerce").dt.strftime("%m/%d/%Y")

    # Rename columns for display
    rename_map = {
        "last_name": "Last Name",
        "first_name": "First Name",
        "medical_record_number": "MRN",
        "medicare_number": "Medicare #",
        "start_of_care_date": "SOC Date",
        "discharge_date": "Discharge",
        "max_bp": "Max BP#",
        "risk_tier": "Risk Tier",
        "total_loc_days": "LOC Days",
        "cap_credit": "Cap Credit",
        "total_net_reimbursement": "Net Reimb.",
        "is_active": "Active",
        "is_inherited": "Inherited",
    }
    display_df = display_df.rename(columns=rename_map)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=600,
    )


def render_risk_analysis(patients_df: pd.DataFrame, summary: dict):
    """Page 3: Risk Tier Analysis."""
    st.header("Risk Tier Analysis")

    if patients_df.empty:
        st.warning("No patient data available.")
        return

    # Risk distribution
    col1, col2, col3 = st.columns(3)
    with col1:
        high = summary.get("high_risk_count", 0)
        st.metric("High Risk (BP >= 5)", high)
        if high > 0:
            st.caption("These patients consume the most cap allowable.")
    with col2:
        watch = summary.get("watch_count", 0)
        st.metric("Watch (BP 3-4)", watch)
    with col3:
        healthy = summary.get("healthy_count", 0)
        st.metric("Healthy (BP 1-2)", healthy)

    # Admissions mix
    st.divider()
    st.subheader("Admissions Mix — Short Stay vs. Long Stay")

    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("Short Stay (BP 1-2)", summary.get("short_stay_count", 0))
        st.metric("Long Stay (BP 3+)", summary.get("long_stay_count", 0))
        pct = summary.get("short_stay_pct", 0)
        st.metric("Short Stay %", f"{pct:.1f}%")
        if summary.get("short_stay_flagged"):
            st.error("Below 60% threshold — admissions mix needs attention.")
        elif not summary.get("short_stay_on_target"):
            st.warning("Below 70% target — monitor admissions mix.")
        else:
            st.success("On target.")

    with col_b:
        # Risk tier pie chart data
        risk_data = pd.DataFrame({
            "Tier": ["High Risk", "Watch", "Healthy"],
            "Count": [
                summary.get("high_risk_count", 0),
                summary.get("watch_count", 0),
                summary.get("healthy_count", 0),
            ],
        })
        risk_data = risk_data[risk_data["Count"] > 0]
        if not risk_data.empty:
            st.bar_chart(risk_data.set_index("Tier"))

    # High risk patient list
    st.divider()
    st.subheader("High Risk Patients (BP >= 5)")
    high_risk = patients_df[patients_df["risk_tier"] == "HIGH"].copy()
    if not high_risk.empty:
        display_cols = ["last_name", "first_name", "medical_record_number",
                        "max_bp", "cap_credit", "total_loc_days", "is_active"]
        available = [c for c in display_cols if c in high_risk.columns]
        st.dataframe(
            high_risk[available].rename(columns={
                "last_name": "Last Name", "first_name": "First Name",
                "medical_record_number": "MRN", "max_bp": "Max BP#",
                "cap_credit": "Cap Credit", "total_loc_days": "LOC Days",
                "is_active": "Active",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.success("No high-risk patients.")


def render_payments(payments_df: pd.DataFrame, monthly_df: pd.DataFrame):
    """Page 4: Payment Detail."""
    st.header("Medicare Claim Payments")

    if payments_df.empty:
        st.warning("No payment data available.")
        return

    # Summary
    total_net = pd.to_numeric(payments_df.get("claim_net_reimbursement", pd.Series(dtype=float)), errors="coerce").sum()
    total_claims = payments_df["claim_number"].nunique() if "claim_number" in payments_df.columns else 0

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Net Reimbursement", f"${total_net:,.2f}")
    with col2:
        st.metric("Unique Claims", f"{total_claims:,}")

    # Monthly trend table
    if not monthly_df.empty:
        st.subheader("Monthly Breakdown")
        display = monthly_df.copy()
        if "month" in display.columns:
            display["month"] = pd.to_datetime(display["month"]).dt.strftime("%b %Y")
        st.dataframe(display, use_container_width=True, hide_index=True)

    # Raw payment data (expandable)
    with st.expander("View All Claim Payments"):
        st.dataframe(payments_df, use_container_width=True, hide_index=True)


def render_export(summary: dict, patients_df: pd.DataFrame,
                  payments_df: pd.DataFrame, monthly_df: pd.DataFrame,
                  cap_year_key: str):
    """Page 5: Export to Excel."""
    st.header("Export Cap Report")

    st.markdown(
        "Generate a formatted Excel workbook with all cap data — "
        "summary, patient detail, payments, and monthly trend."
    )

    if st.button("Generate Excel Report", type="primary"):
        with st.spinner("Building workbook..."):
            output_dir = os.path.join(os.path.dirname(__file__), "..", "output")
            filepath = build_cap_workbook(
                summary=summary,
                patients_df=patients_df,
                payments_df=payments_df,
                monthly_df=monthly_df,
                output_dir=output_dir,
                cap_year_key=cap_year_key,
            )
        st.success(f"Report saved: `{filepath}`")

        # Offer download
        with open(filepath, "rb") as f:
            st.download_button(
                label="Download Excel Report",
                data=f,
                file_name=os.path.basename(filepath),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


# =============================================================================
# MAIN
# =============================================================================

def main():
    cap_year_key = render_sidebar()

    st.title(f"{AGENCY_NAME} — Medicare Hospice Cap Dashboard")

    # Load and process data
    try:
        raw = load_cap_data(cap_year_key)
        processed = process_cap_data(raw, cap_year_key)
    except Exception as e:
        st.error(f"Failed to load data: {e}")
        logger.exception("Data load failed")
        st.stop()

    summary = processed["summary"]
    patients = processed["patients"]
    payments = processed["payments"]
    monthly = processed["monthly"]

    # Tab navigation
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Overview",
        "Patient Detail",
        "Risk Analysis",
        "Payments",
        "Export",
    ])

    with tab1:
        render_overview(summary, monthly)
    with tab2:
        render_patient_detail(patients)
    with tab3:
        render_risk_analysis(patients, summary)
    with tab4:
        render_payments(payments, monthly)
    with tab5:
        render_export(summary, patients, payments, monthly, cap_year_key)


if __name__ == "__main__":
    main()
