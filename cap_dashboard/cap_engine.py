"""
Cap Dashboard — Calculation Engine (v2)
All cap calculations happen here in pandas. No Snowflake writes.

v2: Cap credit is now calculated in SQL using the CMS Patient-by-Patient
Proportional (PP) method directly from FACT_LEVEL_OF_CARE data:
  cap_credit = (LOC days in cap year) / (total lifetime LOC days)

The SQL query in cap_snowflake_client.py returns cap_credit pre-calculated.
This engine enriches with risk tiers, payment data, and summary metrics.
"""
import logging
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from cap_config import (
    CAP_YEARS,
    ACQUISITION_DATE,
    RISK_TIERS,
    ADMISSIONS_TARGET_SHORT_STAY_PCT,
    ADMISSIONS_FLAG_THRESHOLD,
    CAP_ALERT_THRESHOLD,
    CAP_DANGER_THRESHOLD,
    get_risk_tier,
)

logger = logging.getLogger(__name__)


# =============================================================================
# PATIENT-LEVEL PROCESSING
# =============================================================================

def process_cap_patients(patients_df: pd.DataFrame) -> pd.DataFrame:
    """
    Process the patient DataFrame from the v2 SQL query.
    Adds risk tiers, inherited flags, and active status.
    Cap credit comes pre-calculated from SQL.
    """
    if patients_df.empty:
        logger.warning("No patient data provided")
        return pd.DataFrame()

    df = patients_df.copy()

    # Ensure numeric columns
    for col in ["cap_credit", "cap_year_loc_days", "total_lifetime_loc_days",
                 "max_benefit_period", "routine_days", "continuous_days",
                 "respite_days", "gip_days"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Ensure date columns
    for col in ["start_of_care_date", "discharge_date", "death_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Risk tier based on max benefit period
    df["max_bp"] = df["max_benefit_period"].astype(int)
    df["risk_tier"] = df["max_bp"].apply(get_risk_tier)

    # Inherited flag (SOC before acquisition date)
    df["is_inherited"] = df["start_of_care_date"] < pd.Timestamp(ACQUISITION_DATE)

    # Active flag
    today_ts = pd.Timestamp(date.today())
    df["is_active"] = df["discharge_date"].isna() | (df["discharge_date"] > today_ts)

    # Total LOC in cap year (for display)
    df["total_loc_days"] = df["cap_year_loc_days"].astype(int)

    logger.info(
        f"Processed {len(df)} patients. "
        f"Total cap credit: {df['cap_credit'].sum():.4f}, "
        f"Active: {df['is_active'].sum()}, "
        f"High risk: {(df['risk_tier'] == 'HIGH').sum()}"
    )
    return df


# =============================================================================
# PAYMENT AGGREGATION
# =============================================================================

def aggregate_payments(payments_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate claim payments by patient."""
    if payments_df.empty:
        return pd.DataFrame(columns=[
            "nk_patient_key", "total_payment", "total_adjustment",
            "total_net_reimbursement", "claim_count",
        ])

    df = payments_df.copy()
    for col in ["claim_payment_applied", "claim_adjustment", "claim_net_reimbursement"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    agg = (
        df.groupby("nk_patient_key")
        .agg(
            total_payment=("claim_payment_applied", "sum"),
            total_adjustment=("claim_adjustment", "sum"),
            total_net_reimbursement=("claim_net_reimbursement", "sum"),
            claim_count=("claim_number", "nunique"),
        )
        .reset_index()
    )
    return agg


def merge_payments_to_patients(
    patients_df: pd.DataFrame, payments_df: pd.DataFrame
) -> pd.DataFrame:
    """Merge aggregated payments onto patient-level cap data."""
    if patients_df.empty:
        return patients_df
    pay_agg = aggregate_payments(payments_df)
    df = patients_df.merge(pay_agg, on="nk_patient_key", how="left")
    df["total_net_reimbursement"] = df["total_net_reimbursement"].fillna(0)
    df["claim_count"] = df["claim_count"].fillna(0).astype(int)
    return df


# =============================================================================
# CAP POSITION SUMMARY
# =============================================================================

def build_cap_summary(
    patients_df: pd.DataFrame,
    cap_year_key: str,
) -> dict:
    """
    Calculate overall cap position for a given cap year.

    Two views:
    1. Kinnser estimate: from Snowflake data (cap_credit × cap_rate)
    2. PS&R authoritative: from CMS PS&R filing (when available)

    Alert status is driven by PS&R when available.
    """
    params = CAP_YEARS[cap_year_key]
    cap_rate = params["cap_rate"]
    seq_rate = params["sequestration_rate"]
    psr = params.get("psr_actuals")

    if patients_df.empty:
        return _empty_summary(cap_year_key, cap_rate, params)

    # --- Kinnser estimates ---
    est_bene_count = patients_df["cap_credit"].sum()
    est_allowable = est_bene_count * cap_rate

    has_payments = "total_net_reimbursement" in patients_df.columns
    est_net_pymts = patients_df["total_net_reimbursement"].sum() if has_payments else None
    est_gross_pymts = (est_net_pymts / (1 - seq_rate)) if est_net_pymts else None
    est_position = (est_allowable - est_net_pymts) if est_net_pymts is not None else None
    est_utilization = (est_net_pymts / est_allowable * 100) if est_allowable and est_net_pymts else None

    # --- PS&R authoritative (when available) ---
    if psr:
        auth_bene_count = psr["pro_rated_bene"]
        auth_net_pymts = psr["net_reimbursement"]
        auth_gross_pymts = psr["gross_reimbursement"]
        auth_sequestration = psr["sequestration"]
        auth_allowable = auth_bene_count * cap_rate
        auth_position = auth_allowable - auth_net_pymts
        auth_utilization = (auth_net_pymts / auth_allowable * 100) if auth_allowable else None
    else:
        auth_bene_count = None
        auth_net_pymts = None
        auth_gross_pymts = None
        auth_sequestration = None
        auth_allowable = None
        auth_position = None
        auth_utilization = None

    # --- Filter to contributing patients (cap_credit > 0) for summary stats ---
    contributing = patients_df[patients_df["cap_credit"] > 0]

    # --- Risk distribution (contributing patients only) ---
    risk_counts = contributing["risk_tier"].value_counts().to_dict()
    active_count = int(contributing["is_active"].sum())
    inherited_count = int(contributing["is_inherited"].sum())

    # --- Admissions mix (contributing patients only) ---
    short_stay = int((contributing["max_bp"] <= 2).sum())
    long_stay = int((contributing["max_bp"] > 2).sum())
    total = len(contributing)
    short_stay_pct = (short_stay / total * 100) if total else 0

    # --- Alert status (PS&R is authoritative when available) ---
    primary_utilization = auth_utilization if auth_utilization is not None else est_utilization
    primary_position = auth_position if auth_position is not None else est_position

    if primary_position is not None and primary_position < 0:
        alert_status = "DANGER"
    elif primary_utilization is not None:
        if primary_utilization >= CAP_DANGER_THRESHOLD * 100:
            alert_status = "DANGER"
        elif primary_utilization >= CAP_ALERT_THRESHOLD * 100:
            alert_status = "WARNING"
        else:
            alert_status = "OK"
    else:
        alert_status = "UNKNOWN"

    return {
        "cap_year": cap_year_key,
        "cap_year_label": params["label"],
        "cap_rate": cap_rate,
        "sequestration_rate": seq_rate,
        "total_patients": total,
        "active_census": active_count,
        # Kinnser estimates
        "est_bene_count": round(est_bene_count, 4),
        "est_allowable": round(est_allowable, 2),
        "est_net_pymts": round(est_net_pymts, 2) if est_net_pymts is not None else None,
        "est_gross_pymts": round(est_gross_pymts, 2) if est_gross_pymts is not None else None,
        "est_position": round(est_position, 2) if est_position is not None else None,
        "est_utilization_pct": round(est_utilization, 1) if est_utilization is not None else None,
        # PS&R authoritative
        "auth_bene_count": auth_bene_count,
        "auth_allowable": round(auth_allowable, 2) if auth_allowable else None,
        "auth_gross_pymts": auth_gross_pymts,
        "auth_sequestration": auth_sequestration,
        "auth_net_pymts": auth_net_pymts,
        "auth_position": round(auth_position, 2) if auth_position is not None else None,
        "auth_utilization_pct": round(auth_utilization, 1) if auth_utilization is not None else None,
        # Risk distribution
        "high_risk_count": risk_counts.get("HIGH", 0),
        "watch_count": risk_counts.get("WATCH", 0),
        "healthy_count": risk_counts.get("HEALTHY", 0),
        "inherited_count": inherited_count,
        # Admissions mix
        "short_stay_count": short_stay,
        "long_stay_count": long_stay,
        "short_stay_pct": round(short_stay_pct, 1),
        "short_stay_on_target": short_stay_pct >= ADMISSIONS_TARGET_SHORT_STAY_PCT * 100,
        "short_stay_flagged": short_stay_pct < ADMISSIONS_FLAG_THRESHOLD * 100,
        # Alert
        "alert_status": alert_status,
    }


def _empty_summary(cap_year_key: str, cap_rate: float, params: dict) -> dict:
    """Return an empty summary when no patient data is available."""
    return {
        "cap_year": cap_year_key,
        "cap_year_label": params.get("label", cap_year_key),
        "cap_rate": cap_rate,
        "sequestration_rate": params.get("sequestration_rate", 0.02),
        "total_patients": 0,
        "active_census": 0,
        "est_bene_count": 0,
        "est_allowable": 0,
        "est_net_pymts": None,
        "est_position": None,
        "est_utilization_pct": None,
        "auth_bene_count": None, "auth_allowable": None,
        "auth_gross_pymts": None, "auth_sequestration": None,
        "auth_net_pymts": None, "auth_position": None,
        "auth_utilization_pct": None,
        "high_risk_count": 0, "watch_count": 0, "healthy_count": 0,
        "inherited_count": 0,
        "short_stay_count": 0, "long_stay_count": 0, "short_stay_pct": 0,
        "short_stay_on_target": True, "short_stay_flagged": False,
        "alert_status": "UNKNOWN",
    }


# =============================================================================
# TREND HELPERS
# =============================================================================

def calculate_monthly_payments(payments_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate payments by month for trend charting."""
    if payments_df.empty:
        return pd.DataFrame(columns=["month", "total_net_reimbursement", "claim_count"])

    df = payments_df.copy()
    df["check_date"] = pd.to_datetime(df["check_date"], errors="coerce")
    df["claim_net_reimbursement"] = pd.to_numeric(df["claim_net_reimbursement"], errors="coerce").fillna(0)
    df["month"] = df["check_date"].dt.to_period("M").dt.to_timestamp()

    monthly = (
        df.groupby("month")
        .agg(
            total_net_reimbursement=("claim_net_reimbursement", "sum"),
            claim_count=("claim_number", "nunique"),
        )
        .reset_index()
        .sort_values("month")
    )
    return monthly


def calculate_cumulative_position(
    monthly_df: pd.DataFrame, cap_rate: float, est_bene_count: float
) -> pd.DataFrame:
    """Add cumulative reimbursement and cap utilization columns."""
    if monthly_df.empty:
        return monthly_df

    df = monthly_df.copy()
    allowable = est_bene_count * cap_rate
    df["cumulative_reimbursement"] = df["total_net_reimbursement"].cumsum()
    df["cap_allowable"] = allowable
    df["utilization_pct"] = (df["cumulative_reimbursement"] / allowable * 100).round(1) if allowable else 0
    return df
