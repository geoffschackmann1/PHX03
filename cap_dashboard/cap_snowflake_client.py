"""
Cap Dashboard — Snowflake Client (v2)
All Snowflake queries for the Medicare Hospice Cap dashboard.

CRITICAL: The primary driver is FACT_BENEFIT_PERIOD, not FACT_ADMISSION.
A patient is in the cap calculation if they have ANY benefit period days
overlapping the cap year — even if their admission was discharged before
the cap year started.

Reuses the existing SnowflakeClient connection from the QPi project.
Returns pandas DataFrames — all calculations happen in cap_engine.py.
"""
import sys
import os
import logging
from datetime import date

import pandas as pd

# Add parent directory to path so we can import QPi modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from snowflake_client import SnowflakeClient
from cap_config import CLINIC_KEY as HOSPICE_CLINIC_KEY

logger = logging.getLogger(__name__)


def _load_secrets():
    """Load Snowflake credentials into env vars from st.secrets or .env."""
    try:
        import streamlit as st
        if "SF_ACCOUNT" in st.secrets:
            for key in ["SF_ACCOUNT", "SF_USER", "SF_PASSWORD", "SF_ROLE", "SF_WAREHOUSE"]:
                if key in st.secrets:
                    os.environ[key] = str(st.secrets[key])
            return
    except Exception:
        pass
    from load_env import load_dotenv
    load_dotenv()


def _get_client() -> SnowflakeClient:
    """Create and connect a SnowflakeClient using existing QPi config."""
    _load_secrets()
    # Import config AFTER secrets are loaded so env vars are populated
    from config import SNOWFLAKE_CONFIG
    sf = SnowflakeClient(SNOWFLAKE_CONFIG)
    sf.connect()
    return sf


# =============================================================================
# SQL QUERIES — v2: Benefit-period-driven
# =============================================================================

# Primary query: One row per patient, driven by FACT_BENEFIT_PERIOD.
# Aggregates benefit period info AND calculates LOC days within the cap year
# by clipping each LOC record to the cap year window.
SQL_CAP_PATIENTS_V2 = """
WITH cap_patients AS (
    -- All patients with Medicare benefit periods overlapping the cap year
    SELECT DISTINCT bp.NK_PATIENT_KEY, bp.NK_ADMISSION_KEY
    FROM FACT_BENEFIT_PERIOD bp
    JOIN DIM_ORGANIZATION o
        ON bp.NK_BRANCH_KEY = o.NK_BRANCH_KEY
        AND o.IS_DELETED = FALSE
        AND o.NK_CLINIC_KEY = %(clinic_key)s
    JOIN DIM_INSURANCE i
        ON bp.NK_INSURANCE_KEY = i.NK_INSURANCE_KEY
        AND i.IS_DELETED = FALSE
    WHERE bp.IS_DELETED = FALSE
      AND i.INSURANCE_TYPE ILIKE '%%medicare%%'
      AND bp.BENEFIT_PERIOD_START_DATE <= %(cap_end)s
      AND (bp.BENEFIT_PERIOD_END_DATE IS NULL OR bp.BENEFIT_PERIOD_END_DATE >= %(cap_start)s)
),
patient_bp_agg AS (
    -- Aggregate benefit period info per patient
    SELECT
        bp.NK_PATIENT_KEY,
        MAX(bp.BENEFIT_PERIOD_NUMBER) AS max_benefit_period,
        MIN(bp.BENEFIT_PERIOD_START_DATE) AS earliest_bp_start,
        MAX(bp.BENEFIT_PERIOD_END_DATE) AS latest_bp_end,
        COUNT(DISTINCT bp.NK_BENEFIT_PERIOD_KEY) AS bp_count_in_cap_year
    FROM FACT_BENEFIT_PERIOD bp
    JOIN cap_patients cp ON bp.NK_PATIENT_KEY = cp.NK_PATIENT_KEY
    JOIN DIM_INSURANCE i ON bp.NK_INSURANCE_KEY = i.NK_INSURANCE_KEY
        AND i.IS_DELETED = FALSE
    WHERE bp.IS_DELETED = FALSE
      AND i.INSURANCE_TYPE ILIKE '%%medicare%%'
      AND bp.BENEFIT_PERIOD_START_DATE <= %(cap_end)s
      AND (bp.BENEFIT_PERIOD_END_DATE IS NULL OR bp.BENEFIT_PERIOD_END_DATE >= %(cap_start)s)
    GROUP BY bp.NK_PATIENT_KEY
),
loc_in_cap_year AS (
    -- LOC days clipped to the cap year window per patient
    SELECT
        loc.NK_PATIENT_KEY,
        SUM(
            DATEDIFF('day',
                GREATEST(loc.START_DATE, %(cap_start)s::DATE),
                LEAST(COALESCE(loc.END_DATE, CURRENT_DATE()), %(cap_end)s::DATE)
            ) + 1
        ) AS cap_year_loc_days,
        SUM(CASE WHEN loc.LEVEL_OF_CARE = 'ROUTINE'
            THEN DATEDIFF('day',
                GREATEST(loc.START_DATE, %(cap_start)s::DATE),
                LEAST(COALESCE(loc.END_DATE, CURRENT_DATE()), %(cap_end)s::DATE)
            ) + 1 ELSE 0 END) AS routine_days,
        SUM(CASE WHEN loc.LEVEL_OF_CARE = 'CONTINUOUS'
            THEN DATEDIFF('day',
                GREATEST(loc.START_DATE, %(cap_start)s::DATE),
                LEAST(COALESCE(loc.END_DATE, CURRENT_DATE()), %(cap_end)s::DATE)
            ) + 1 ELSE 0 END) AS continuous_days,
        SUM(CASE WHEN loc.LEVEL_OF_CARE = 'RESPITE'
            THEN DATEDIFF('day',
                GREATEST(loc.START_DATE, %(cap_start)s::DATE),
                LEAST(COALESCE(loc.END_DATE, CURRENT_DATE()), %(cap_end)s::DATE)
            ) + 1 ELSE 0 END) AS respite_days,
        SUM(CASE WHEN loc.LEVEL_OF_CARE ILIKE '%%GIP%%'
            THEN DATEDIFF('day',
                GREATEST(loc.START_DATE, %(cap_start)s::DATE),
                LEAST(COALESCE(loc.END_DATE, CURRENT_DATE()), %(cap_end)s::DATE)
            ) + 1 ELSE 0 END) AS gip_days
    FROM FACT_LEVEL_OF_CARE loc
    JOIN cap_patients cp ON loc.NK_PATIENT_KEY = cp.NK_PATIENT_KEY
    JOIN DIM_ORGANIZATION o ON loc.NK_BRANCH_KEY = o.NK_BRANCH_KEY
        AND o.IS_DELETED = FALSE
        AND o.NK_CLINIC_KEY = %(clinic_key)s
    WHERE loc.IS_DELETED = FALSE
      AND loc.START_DATE <= %(cap_end)s
      AND (loc.END_DATE IS NULL OR loc.END_DATE >= %(cap_start)s)
    GROUP BY loc.NK_PATIENT_KEY
),
total_lifetime_loc AS (
    -- Total lifetime LOC days per patient (across ALL cap years, all time)
    SELECT
        loc.NK_PATIENT_KEY,
        SUM(
            DATEDIFF('day', loc.START_DATE,
                COALESCE(loc.END_DATE, CURRENT_DATE())
            ) + 1
        ) AS total_loc_days
    FROM FACT_LEVEL_OF_CARE loc
    JOIN cap_patients cp ON loc.NK_PATIENT_KEY = cp.NK_PATIENT_KEY
    JOIN DIM_ORGANIZATION o ON loc.NK_BRANCH_KEY = o.NK_BRANCH_KEY
        AND o.IS_DELETED = FALSE
        AND o.NK_CLINIC_KEY = %(clinic_key)s
    WHERE loc.IS_DELETED = FALSE
    GROUP BY loc.NK_PATIENT_KEY
)
SELECT
    p.NK_PATIENT_KEY,
    p.MEDICAL_RECORD_NUMBER,
    p.MEDICARE_NUMBER,
    p.LAST_NAME,
    p.FIRST_NAME,
    p.DATE_OF_BIRTH,
    p.DEATH_DATE,
    -- Admission info (most recent)
    a.NK_ADMISSION_KEY,
    a.START_OF_CARE_DATE,
    a.DISCHARGE_DATE,
    ad.DISCHARGE_REASON,
    -- Benefit period aggregates
    bpa.max_benefit_period,
    bpa.bp_count_in_cap_year,
    -- LOC days in cap year (clipped)
    COALESCE(lcy.cap_year_loc_days, 0) AS cap_year_loc_days,
    COALESCE(lcy.routine_days, 0) AS routine_days,
    COALESCE(lcy.continuous_days, 0) AS continuous_days,
    COALESCE(lcy.respite_days, 0) AS respite_days,
    COALESCE(lcy.gip_days, 0) AS gip_days,
    -- Total lifetime LOC days (for cap credit calculation)
    COALESCE(tl.total_loc_days, 0) AS total_lifetime_loc_days,
    -- Cap credit: CMS PP method = cap_year_days / total_lifetime_days
    CASE
        WHEN COALESCE(tl.total_loc_days, 0) > 0
        THEN LEAST(COALESCE(lcy.cap_year_loc_days, 0)::FLOAT
                    / tl.total_loc_days::FLOAT, 1.0)
        ELSE 0.0
    END AS cap_credit
FROM cap_patients cp
JOIN DIM_PATIENT_DETAIL p
    ON cp.NK_PATIENT_KEY = p.NK_PATIENT_KEY
    AND p.IS_DELETED = FALSE
LEFT JOIN patient_bp_agg bpa
    ON cp.NK_PATIENT_KEY = bpa.NK_PATIENT_KEY
LEFT JOIN FACT_ADMISSION a
    ON cp.NK_ADMISSION_KEY = a.NK_ADMISSION_KEY
    AND a.IS_DELETED = FALSE
LEFT JOIN DIM_ADMISSION_DETAIL ad
    ON a.NK_ADMISSION_KEY = ad.NK_ADMISSION_KEY
    AND ad.IS_DELETED = FALSE
LEFT JOIN loc_in_cap_year lcy
    ON cp.NK_PATIENT_KEY = lcy.NK_PATIENT_KEY
LEFT JOIN total_lifetime_loc tl
    ON cp.NK_PATIENT_KEY = tl.NK_PATIENT_KEY
ORDER BY p.LAST_NAME, p.FIRST_NAME
"""

SQL_BENEFIT_PERIODS = """
SELECT
    bp.NK_BENEFIT_PERIOD_KEY,
    bp.NK_ADMISSION_KEY,
    bp.NK_PATIENT_KEY,
    bp.BENEFIT_PERIOD_NUMBER,
    bp.BENEFIT_PERIOD_START_DATE,
    bp.BENEFIT_PERIOD_END_DATE,
    bp.IS_BEREAVEMENT,
    i.INSURANCE_TYPE
FROM FACT_BENEFIT_PERIOD bp
JOIN DIM_ORGANIZATION o
    ON bp.NK_BRANCH_KEY = o.NK_BRANCH_KEY
    AND o.IS_DELETED = FALSE
    AND o.NK_CLINIC_KEY = %(clinic_key)s
    AND o.CLINIC_TYPE = 'HOSPICE'
JOIN DIM_INSURANCE i
    ON bp.NK_INSURANCE_KEY = i.NK_INSURANCE_KEY
    AND i.IS_DELETED = FALSE
WHERE bp.IS_DELETED = FALSE
  AND i.INSURANCE_TYPE ILIKE '%%medicare%%'
  AND bp.BENEFIT_PERIOD_START_DATE <= %(cap_end)s
  AND (bp.BENEFIT_PERIOD_END_DATE IS NULL OR bp.BENEFIT_PERIOD_END_DATE >= %(cap_start)s)
ORDER BY bp.NK_PATIENT_KEY, bp.BENEFIT_PERIOD_NUMBER
"""

SQL_CLAIM_PAYMENTS = """
SELECT
    fcp.NK_PATIENT_KEY,
    fcp.MEDICAL_RECORD_NUMBER,
    fcp.PATIENT_LAST_NAME,
    fcp.PATIENT_FIRST_NAME,
    fcp.INSURANCE_TYPE,
    fcp.CLAIM_NUMBER,
    fcp.CLAIM_START_DATE,
    fcp.CLAIM_END_DATE,
    fcp.CHECK_DATE,
    fcp.DEPOSIT_DATE,
    fcp.CLAIM_PAYMENT_APPLIED,
    fcp.CLAIM_ADJUSTMENT,
    fcp.CLAIM_NET_REIMBURSEMENT,
    fcp.TYPE_OF_BILL
FROM FACT_FINANCIAL_CLAIM_PAYMENT fcp
JOIN DIM_ORGANIZATION o
    ON fcp.NK_BRANCH_KEY = o.NK_BRANCH_KEY
    AND o.IS_DELETED = FALSE
    AND o.NK_CLINIC_KEY = %(clinic_key)s
WHERE fcp.INSURANCE_TYPE ILIKE '%%medicare%%'
  AND fcp.CHECK_DATE BETWEEN %(cap_start)s AND %(cap_end)s
ORDER BY fcp.PATIENT_LAST_NAME, fcp.CHECK_DATE
"""

SQL_CENSUS_SNAPSHOT = """
SELECT
    c.NK_PATIENT_KEY,
    c.NK_ADMISSION_KEY,
    c.NK_EPISODE_KEY,
    c.CENSUS_DATE,
    c.IS_DISCHARGED,
    i.INSURANCE_TYPE
FROM FACT_CENSUS c
JOIN DIM_ORGANIZATION o
    ON c.NK_BRANCH_KEY = o.NK_BRANCH_KEY
    AND o.IS_DELETED = FALSE
    AND o.NK_CLINIC_KEY = %(clinic_key)s
JOIN DIM_INSURANCE i
    ON c.NK_INSURANCE_KEY = i.NK_INSURANCE_KEY
    AND i.IS_DELETED = FALSE
WHERE c.IS_DELETED = FALSE
  AND i.INSURANCE_TYPE ILIKE '%%medicare%%'
  AND c.CENSUS_DATE = %(snapshot_date)s
  AND c.IS_DISCHARGED = FALSE
"""


class CapSnowflakeClient:
    """Read-only Snowflake queries for the Medicare Hospice Cap dashboard."""

    def __init__(self):
        self.sf: SnowflakeClient = None

    def connect(self) -> "CapSnowflakeClient":
        self.sf = _get_client()
        return self

    def close(self):
        if self.sf:
            self.sf.close()

    def _query(self, sql: str, params: dict) -> pd.DataFrame:
        """Execute query and return DataFrame."""
        cur = self.sf.conn.cursor()
        try:
            cur.execute(sql, params)
            cols = [desc[0].lower() for desc in cur.description]
            rows = cur.fetchall()
            return pd.DataFrame(rows, columns=cols)
        finally:
            cur.close()

    def _cap_params(self, cap_start: date, cap_end: date, clinic_key: int) -> dict:
        return {
            "cap_start": cap_start.isoformat(),
            "cap_end": cap_end.isoformat(),
            "clinic_key": clinic_key,
        }

    # -----------------------------------------------------------------
    # Public query methods
    # -----------------------------------------------------------------

    def get_cap_patients(
        self, cap_start: date, cap_end: date, clinic_key: int = HOSPICE_CLINIC_KEY
    ) -> pd.DataFrame:
        """
        All Medicare patients with benefit periods overlapping the cap year.
        Returns one row per patient with cap credit pre-calculated in SQL.
        """
        logger.info(f"Querying cap patients (BP-driven): {cap_start} to {cap_end}")
        df = self._query(SQL_CAP_PATIENTS_V2, self._cap_params(cap_start, cap_end, clinic_key))
        # Dedup to one row per patient (cap_patients CTE may produce dupes via admission join)
        if not df.empty:
            df = df.drop_duplicates(subset=["nk_patient_key"], keep="first")
        logger.info(f"  → {len(df)} unique patients")
        return df

    def get_benefit_periods(
        self, cap_start: date, cap_end: date, clinic_key: int = HOSPICE_CLINIC_KEY
    ) -> pd.DataFrame:
        """Benefit period detail for cap year patients."""
        logger.info(f"Querying benefit periods: {cap_start} to {cap_end}")
        df = self._query(SQL_BENEFIT_PERIODS, self._cap_params(cap_start, cap_end, clinic_key))
        logger.info(f"  → {len(df)} benefit period records")
        return df

    def get_claim_payments(
        self, cap_start: date, cap_end: date, clinic_key: int = HOSPICE_CLINIC_KEY
    ) -> pd.DataFrame:
        """Medicare claim payments within the cap year."""
        logger.info(f"Querying claim payments: {cap_start} to {cap_end}")
        df = self._query(SQL_CLAIM_PAYMENTS, self._cap_params(cap_start, cap_end, clinic_key))
        logger.info(f"  → {len(df)} payment records")
        return df

    def get_census_snapshot(
        self, snapshot_date: date, clinic_key: int = HOSPICE_CLINIC_KEY
    ) -> pd.DataFrame:
        """Medicare census on a specific date."""
        logger.info(f"Querying Medicare census snapshot: {snapshot_date}")
        params = {
            "snapshot_date": snapshot_date.isoformat(),
            "clinic_key": clinic_key,
        }
        df = self._query(SQL_CENSUS_SNAPSHOT, params)
        logger.info(f"  → {len(df)} active Medicare patients")
        return df

    def get_all_cap_data(
        self, cap_start: date, cap_end: date, clinic_key: int = HOSPICE_CLINIC_KEY
    ) -> dict:
        """Run all cap queries and return dict of DataFrames."""
        return {
            "patients": self.get_cap_patients(cap_start, cap_end, clinic_key),
            "benefit_periods": self.get_benefit_periods(cap_start, cap_end, clinic_key),
            "payments": self.get_claim_payments(cap_start, cap_end, clinic_key),
            "census": self.get_census_snapshot(date.today(), clinic_key),
        }
