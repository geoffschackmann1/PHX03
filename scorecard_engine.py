"""
QPi Scorecard Automation — Scorecard Engine
Merges Snowflake + iSolved data and calculates all derived KPIs.
Produces clinician-level and agency-level scorecard DataFrames.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from config import (
    VISIT_POINTS, BEST_PRACTICE_HOURS, STANDARD_HOURS, STANDARD_POINTS_TARGET,
    TARGETS, ASSISTANT_DISCIPLINES, OPP_AMOUNT, CLINICIAN_NAME_ALIASES,
)

logger = logging.getLogger(__name__)


@dataclass
class AgencyMetrics:
    """Agency-level metrics pulled from Snowflake."""
    census: int = 0
    lupa_pct: float = 0.0
    lupa_episodes: int = 0
    lupa_total: int = 0
    non_admits: int = 0
    visits_per_episode: float = 0.0
    vpe_episodes: int = 0
    soc_medicare_pct: float = 0.0
    soc_medicare_ct: int = 0
    soc_total: int = 0
    assistant_visits: int = 0
    skilled_visits: int = 0
    assistant_pct: float = 0.0


def build_clinician_scorecard(
    sf_productivity: pd.DataFrame,
    sf_documentation: pd.DataFrame,
    payroll: pd.DataFrame,
    prior_productivity: Optional[pd.DataFrame] = None,
    prior_documentation: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Merge Snowflake productivity + documentation with iSolved payroll.
    Calculate all derived KPIs per clinician.
    Returns a single DataFrame with one row per clinician.
    """
    logger.info("Building clinician scorecard...")

    # ------------------------------------------------------------------
    # 1. Start with Snowflake productivity
    # ------------------------------------------------------------------
    df = sf_productivity.copy()

    # Calculate points and best practice hours
    df["points"] = (
        df["soc_eval_ct"] * VISIT_POINTS["ADMISSION/ EVAL"]
        + df["recert_ct"] * VISIT_POINTS["RECERTIFICATION/ REEVAL"]
        + df["routine_ct"] * VISIT_POINTS["ROUTINE VISIT"]
        + df["discharge_ct"] * VISIT_POINTS["DISCHARGE"]
    )

    df["bp_hours"] = (
        df["soc_eval_ct"] * BEST_PRACTICE_HOURS["ADMISSION/ EVAL"]
        + df["recert_ct"] * BEST_PRACTICE_HOURS["RECERTIFICATION/ REEVAL"]
        + df["routine_ct"] * BEST_PRACTICE_HOURS["ROUTINE VISIT"]
        + df["discharge_ct"] * BEST_PRACTICE_HOURS["DISCHARGE"]
    )

    df["visits_per_patient"] = (
        df["total_visits"] / df["unique_patients"].replace(0, pd.NA)
    ).round(1)

    # ------------------------------------------------------------------
    # 2. Merge documentation
    # ------------------------------------------------------------------
    if not sf_documentation.empty:
        doc_cols = ["clinician_name", "total_docs", "docs_on_time", "docs_late",
                    "on_time_pct", "avg_days_to_submit", "fastest_days", "slowest_days"]
        doc_available = [c for c in doc_cols if c in sf_documentation.columns]
        df = df.merge(
            sf_documentation[doc_available],
            on="clinician_name",
            how="left",
            suffixes=("", "_doc"),
        )
    else:
        df["total_docs"] = df["total_visits"]
        df["docs_on_time"] = 0
        df["on_time_pct"] = 0.0

    # ------------------------------------------------------------------
    # 3. Merge iSolved payroll
    # ------------------------------------------------------------------
    if not payroll.empty:
        # Normalize names for matching
        df["_join_name"] = df["clinician_name"].str.upper().str.strip()
        payroll["_join_name"] = payroll["clinician_name"].str.upper().str.strip()

        # Apply name aliases (iSolved name -> Snowflake name)
        if CLINICIAN_NAME_ALIASES:
            payroll["_join_name"] = payroll["_join_name"].replace(CLINICIAN_NAME_ALIASES)

        # Log unmatched names for debugging
        sf_names = set(df["_join_name"].dropna())
        pr_names = set(payroll["_join_name"].dropna())
        unmatched_pr = pr_names - sf_names
        unmatched_sf = sf_names - pr_names
        if unmatched_pr:
            logger.warning(f"iSolved names not in Snowflake: {sorted(unmatched_pr)}")
        if unmatched_sf:
            logger.info(f"Snowflake clinicians without payroll data: {sorted(unmatched_sf)}")

        payroll_cols = [
            "_join_name", "regular_hours", "overtime_hours", "gross_wages",
            "mileage", "total_cost", "vacation_hours", "pto_hours",
            "sick_hours", "holiday_hours", "bereavement_hours", "time_off_hours",
            "on_call_weekday", "on_call_weekend", "on_call_pay", "pay_type",
        ]
        payroll_available = [c for c in payroll_cols if c in payroll.columns]

        df = df.merge(
            payroll[payroll_available],
            on="_join_name",
            how="left",
        )
        df.drop(columns=["_join_name"], inplace=True, errors="ignore")
    else:
        # No payroll data — add empty columns
        for col in ["regular_hours", "overtime_hours", "gross_wages", "mileage",
                     "total_cost", "vacation_hours", "pto_hours", "sick_hours",
                     "holiday_hours", "bereavement_hours", "time_off_hours"]:
            df[col] = pd.NA

    # Clean up payroll columns
    payroll_float_cols = [
        "regular_hours", "overtime_hours", "gross_wages", "mileage",
        "total_cost", "vacation_hours", "pto_hours", "sick_hours",
        "holiday_hours", "bereavement_hours", "time_off_hours",
    ]
    for col in payroll_float_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # ------------------------------------------------------------------
    # 4. Calculate derived KPIs
    # ------------------------------------------------------------------

    # Time off total
    df["time_off_hours"] = (
        df["vacation_hours"] + df["pto_hours"] + df["sick_hours"]
        + df["holiday_hours"] + df["bereavement_hours"]
    )

    # Adjusted productivity target
    df["available_hours"] = STANDARD_HOURS - df["time_off_hours"]
    df["adj_target"] = (STANDARD_POINTS_TARGET * df["available_hours"] / STANDARD_HOURS).round(1)
    df["prod_pct"] = (df["points"] / df["adj_target"].replace(0, pd.NA) * 100).round(1)
    df["meets_target"] = df["points"] >= df["adj_target"]

    # Utilization (actual hours / best practice hours)
    df["utilization_pct"] = pd.NA
    mask_hrs = df["regular_hours"] > 0
    df.loc[mask_hrs, "utilization_pct"] = (
        df.loc[mask_hrs, "regular_hours"] / df.loc[mask_hrs, "bp_hours"].replace(0, pd.NA) * 100
    ).round(1)

    # Points per hour
    df["points_per_hour"] = pd.NA
    df.loc[mask_hrs, "points_per_hour"] = (
        df.loc[mask_hrs, "points"] / df.loc[mask_hrs, "regular_hours"]
    ).round(2)

    # Cost per visit
    df["cpv"] = pd.NA
    mask_cost = (df["total_cost"] > 0) & (df["total_visits"] > 0)
    df.loc[mask_cost, "cpv"] = (
        df.loc[mask_cost, "total_cost"] / df.loc[mask_cost, "total_visits"]
    ).round(2)

    # Missed visit %
    df["missed_visit_pct"] = (
        df["missed_visits"] / (df["total_visits"] + df["missed_visits"]).replace(0, pd.NA) * 100
    ).round(1).fillna(0)

    # OPP eligibility
    df["opp_eligible"] = (
        df["meets_target"]
        & df["utilization_pct"].notna()
        & (df["utilization_pct"] >= 100)
    )
    df["opp_amount"] = df["opp_eligible"].apply(lambda x: OPP_AMOUNT if x else 0)

    # ------------------------------------------------------------------
    # 5. Prior period trending (if available)
    # ------------------------------------------------------------------
    if prior_productivity is not None and not prior_productivity.empty:
        prior = prior_productivity.copy()
        prior["prior_points"] = (
            prior["soc_eval_ct"] * VISIT_POINTS["ADMISSION/ EVAL"]
            + prior["recert_ct"] * VISIT_POINTS["RECERTIFICATION/ REEVAL"]
            + prior["routine_ct"] * VISIT_POINTS["ROUTINE VISIT"]
            + prior["discharge_ct"] * VISIT_POINTS["DISCHARGE"]
        )
        prior["prior_visits"] = prior["total_visits"]

        prior_slim = prior[["clinician_name", "prior_points", "prior_visits"]].copy()
        df = df.merge(prior_slim, on="clinician_name", how="left")

        df["points_trend"] = df["points"] - df["prior_points"].fillna(0)
        df["visits_trend"] = df["total_visits"] - df["prior_visits"].fillna(0)
    else:
        df["prior_points"] = pd.NA
        df["prior_visits"] = pd.NA
        df["points_trend"] = pd.NA
        df["visits_trend"] = pd.NA

    if prior_documentation is not None and not prior_documentation.empty:
        prior_doc = prior_documentation[["clinician_name", "on_time_pct"]].copy()
        prior_doc = prior_doc.rename(columns={"on_time_pct": "prior_on_time_pct"})
        df = df.merge(prior_doc, on="clinician_name", how="left")
        df["doc_trend"] = df["on_time_pct"] - df["prior_on_time_pct"].fillna(0)
    else:
        df["prior_on_time_pct"] = pd.NA
        df["doc_trend"] = pd.NA

    # ------------------------------------------------------------------
    # 6. Sort and return
    # ------------------------------------------------------------------
    df = df.sort_values("points", ascending=False).reset_index(drop=True)

    logger.info(f"Scorecard built: {len(df)} clinicians, "
                f"{df['total_visits'].sum()} total visits, "
                f"{df['meets_target'].sum()}/{len(df)} meet target")

    return df


def build_agency_metrics(sf_data: dict) -> AgencyMetrics:
    """Extract agency-level metrics from Snowflake query results."""
    m = AgencyMetrics()

    # Census
    if not sf_data["census"].empty:
        m.census = int(sf_data["census"].iloc[0].get("census_count", 0))

    # LUPA
    if not sf_data["lupa"].empty:
        row = sf_data["lupa"].iloc[0]
        m.lupa_pct = float(row.get("lupa_pct", 0) or 0)
        m.lupa_episodes = int(row.get("lupa_episodes", 0) or 0)
        m.lupa_total = int(row.get("total_episodes", 0) or 0)

    # Non-admits
    if not sf_data["non_admits"].empty:
        m.non_admits = int(sf_data["non_admits"].iloc[0].get("non_admit_count", 0))

    # Visits per episode
    if not sf_data["visits_per_episode"].empty:
        row = sf_data["visits_per_episode"].iloc[0]
        m.visits_per_episode = float(row.get("visits_per_episode", 0) or 0)
        m.vpe_episodes = int(row.get("total_episodes", 0) or 0)

    # SoC Medicare
    if not sf_data["soc_medicare"].empty:
        row = sf_data["soc_medicare"].iloc[0]
        m.soc_medicare_pct = float(row.get("medicare_pct", 0) or 0)
        m.soc_medicare_ct = int(row.get("medicare_socs", 0) or 0)
        m.soc_total = int(row.get("total_socs", 0) or 0)

    # Assistant utilization
    if not sf_data["assistant_util"].empty:
        for _, row in sf_data["assistant_util"].iterrows():
            if row.get("skill_level") == "ASSISTANT":
                m.assistant_visits = int(row.get("visit_count", 0))
            else:
                m.skilled_visits = int(row.get("visit_count", 0))
        total = m.assistant_visits + m.skilled_visits
        m.assistant_pct = round(m.assistant_visits / total * 100, 1) if total > 0 else 0

    return m


def evaluate_target(value, target_key: str) -> str:
    """Evaluate a value against a KPI target. Returns 'MEETS', 'BELOW', or 'ABOVE'."""
    if value is None or pd.isna(value):
        return "N/A"

    tgt = TARGETS.get(target_key)
    if not tgt:
        return "N/A"

    t = tgt["value"]
    d = tgt["direction"]

    if d == "gte":
        return "MEETS" if value >= t else "BELOW"
    elif d == "gt":
        return "MEETS" if value > t else "BELOW"
    elif d == "lte":
        return "MEETS" if value <= t else "ABOVE"
    elif d == "lt":
        return "MEETS" if value < t else "ABOVE"
    else:
        return "N/A"
