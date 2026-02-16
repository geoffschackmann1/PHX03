"""
QPi Scorecard Automation — Snowflake Client
Connects to Snowflake (read-only) and runs all scorecard queries.
Returns pandas DataFrames.
"""
import logging
from datetime import date
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# =============================================================================
# SQL QUERIES
# =============================================================================

SQL_CLINICIAN_PRODUCTIVITY = """
WITH clinical_visits AS (
    SELECT
        u.FULL_NAME AS clinician_name,
        u.USER_TYPE AS discipline,
        u.EMPLOYEE_TYPE AS emp_type,
        d.VISIT_CATEGORY,
        f.IS_MISSED_VISIT,
        f.NK_PATIENT_KEY,
        f.NK_PATIENT_TASK_KEY,
        f.VISIT_DATE,
        f.FIRST_SUBMITTED_WITH_SIGNATURE_DATE,
        f.IS_OASIS,
        CASE WHEN u.USER_TYPE IN ('PTA','COTA','LPN/LVN','HHA')
             THEN 'ASSISTANT' ELSE 'SKILLED' END AS skill_level
    FROM FACT_PATIENT_TASK f
    JOIN DIM_PATIENT_TASK_DETAIL d ON f.NK_PATIENT_TASK_KEY = d.NK_PATIENT_TASK_KEY
    JOIN DIM_USER u ON f.NK_CLINICIAN_USERS_KEY = u.NK_USERS_KEY
    JOIN DIM_ORGANIZATION o ON f.NK_BRANCH_KEY = o.NK_BRANCH_KEY
    WHERE f.IS_DELETED = FALSE
      AND d.IS_DELETED = FALSE
      AND o.NK_CLINIC_KEY = %(clinic_key)s
      AND o.CLINIC_TYPE = 'AGENCY MANAGER'
      AND f.VISIT_DATE BETWEEN %(pp_start)s AND %(pp_end)s
      AND f.VISIT_DATE <= CURRENT_DATE()
      AND d.IS_COMPLETED = TRUE
      AND d.TASK_TYPE_ABBREVIATION IN ('SN','PT','OT','ST','MSW','LPN/LVN','PTA','COTA','HHA')
      AND d.VISIT_CATEGORY IN ('ADMISSION/ EVAL','RECERTIFICATION/ REEVAL','ROUTINE VISIT','DISCHARGE')
)
SELECT
    clinician_name,
    discipline,
    emp_type,
    skill_level,
    COUNT(CASE WHEN VISIT_CATEGORY = 'ADMISSION/ EVAL' AND IS_MISSED_VISIT = FALSE THEN 1 END) AS soc_eval_ct,
    COUNT(CASE WHEN VISIT_CATEGORY = 'RECERTIFICATION/ REEVAL' AND IS_MISSED_VISIT = FALSE THEN 1 END) AS recert_ct,
    COUNT(CASE WHEN VISIT_CATEGORY = 'ROUTINE VISIT' AND IS_MISSED_VISIT = FALSE THEN 1 END) AS routine_ct,
    COUNT(CASE WHEN VISIT_CATEGORY = 'DISCHARGE' AND IS_MISSED_VISIT = FALSE THEN 1 END) AS discharge_ct,
    COUNT(CASE WHEN IS_MISSED_VISIT = FALSE THEN 1 END) AS total_visits,
    COUNT(CASE WHEN IS_MISSED_VISIT = TRUE THEN 1 END) AS missed_visits,
    COUNT(DISTINCT CASE WHEN IS_MISSED_VISIT = FALSE THEN NK_PATIENT_KEY END) AS unique_patients,
    ROUND(COUNT(CASE WHEN VISIT_CATEGORY = 'ADMISSION/ EVAL' AND IS_MISSED_VISIT = FALSE THEN 1 END)::FLOAT
          / NULLIF(COUNT(CASE WHEN IS_MISSED_VISIT = FALSE THEN 1 END), 0) * 100, 1) AS pct_soc_eval,
    ROUND(COUNT(CASE WHEN VISIT_CATEGORY = 'ROUTINE VISIT' AND IS_MISSED_VISIT = FALSE THEN 1 END)::FLOAT
          / NULLIF(COUNT(CASE WHEN IS_MISSED_VISIT = FALSE THEN 1 END), 0) * 100, 1) AS pct_routine
FROM clinical_visits
GROUP BY clinician_name, discipline, emp_type, skill_level
ORDER BY total_visits DESC
"""

SQL_DOCUMENTATION = """
WITH doc_metrics AS (
    SELECT
        u.FULL_NAME AS clinician_name,
        u.USER_TYPE AS discipline,
        f.NK_PATIENT_TASK_KEY,
        f.VISIT_DATE,
        f.FIRST_SUBMITTED_WITH_SIGNATURE_DATE,
        CASE
            WHEN f.FIRST_SUBMITTED_WITH_SIGNATURE_DATE IS NOT NULL
                 AND DATEDIFF('day', f.VISIT_DATE,
                     f.FIRST_SUBMITTED_WITH_SIGNATURE_DATE) <= 2
            THEN 1 ELSE 0
        END AS is_on_time,
        DATEDIFF('day', f.VISIT_DATE,
                 f.FIRST_SUBMITTED_WITH_SIGNATURE_DATE) AS days_to_submit
    FROM FACT_PATIENT_TASK f
    JOIN DIM_PATIENT_TASK_DETAIL d ON f.NK_PATIENT_TASK_KEY = d.NK_PATIENT_TASK_KEY
    JOIN DIM_USER u ON f.NK_CLINICIAN_USERS_KEY = u.NK_USERS_KEY
    JOIN DIM_ORGANIZATION o ON f.NK_BRANCH_KEY = o.NK_BRANCH_KEY
    WHERE f.IS_DELETED = FALSE
      AND d.IS_DELETED = FALSE
      AND o.NK_CLINIC_KEY = %(clinic_key)s
      AND o.CLINIC_TYPE = 'AGENCY MANAGER'
      AND f.VISIT_DATE BETWEEN %(pp_start)s AND %(pp_end)s
      AND f.VISIT_DATE <= CURRENT_DATE()
      AND d.IS_COMPLETED = TRUE
      AND f.IS_MISSED_VISIT = FALSE
      AND d.TASK_TYPE_ABBREVIATION IN ('SN','PT','OT','ST','MSW','LPN/LVN','PTA','COTA','HHA')
      AND d.VISIT_CATEGORY IN ('ADMISSION/ EVAL','RECERTIFICATION/ REEVAL','ROUTINE VISIT','DISCHARGE')
)
SELECT
    clinician_name,
    discipline,
    COUNT(*) AS total_docs,
    SUM(is_on_time) AS docs_on_time,
    COUNT(*) - SUM(is_on_time) AS docs_late,
    ROUND(SUM(is_on_time)::FLOAT / NULLIF(COUNT(*), 0) * 100, 1) AS on_time_pct,
    ROUND(AVG(days_to_submit), 1) AS avg_days_to_submit,
    MIN(days_to_submit) AS fastest_days,
    MAX(days_to_submit) AS slowest_days
FROM doc_metrics
GROUP BY clinician_name, discipline
ORDER BY on_time_pct ASC
"""

SQL_CENSUS = """
SELECT COUNT(DISTINCT c.NK_PATIENT_KEY) AS census_count
FROM FACT_CENSUS c
JOIN DIM_ORGANIZATION o ON c.NK_BRANCH_KEY = o.NK_BRANCH_KEY
WHERE c.IS_DELETED = FALSE
  AND o.NK_CLINIC_KEY = %(clinic_key)s
  AND c.IS_DISCHARGED = FALSE
  AND c.CENSUS_DATE = %(pp_end)s
"""

SQL_LUPA = """
SELECT
    COUNT(*) AS total_episodes,
    SUM(CASE WHEN IS_LUPA = TRUE THEN 1 ELSE 0 END) AS lupa_episodes,
    ROUND(SUM(CASE WHEN IS_LUPA = TRUE THEN 1 ELSE 0 END)::FLOAT
          / NULLIF(COUNT(*), 0) * 100, 1) AS lupa_pct
FROM FACT_PDGM_FINANCIAL p
JOIN DIM_ORGANIZATION o ON p.NK_BRANCH_KEY = o.NK_BRANCH_KEY
WHERE p.IS_DELETED = FALSE
  AND o.NK_CLINIC_KEY = %(clinic_key)s
  AND p.BILLING_PERIOD_END_DATE BETWEEN
      DATEADD('month', -6, %(pp_end)s::DATE) AND %(pp_end)s::DATE
"""

SQL_NON_ADMITS = """
SELECT COUNT(*) AS non_admit_count
FROM FACT_PATIENT_NONADMIT n
JOIN DIM_ORGANIZATION o ON n.NK_BRANCH_KEY = o.NK_BRANCH_KEY
WHERE n.IS_DELETED = FALSE
  AND o.NK_CLINIC_KEY = %(clinic_key)s
  AND n.NONADMIT_DATE BETWEEN %(pp_start)s AND %(pp_end)s
"""

SQL_VISITS_PER_EPISODE = """
SELECT
    COUNT(DISTINCT f.NK_EPISODE_KEY) AS total_episodes,
    COUNT(f.NK_PATIENT_TASK_KEY) AS total_visits,
    ROUND(COUNT(f.NK_PATIENT_TASK_KEY)::FLOAT
          / NULLIF(COUNT(DISTINCT f.NK_EPISODE_KEY), 0), 1) AS visits_per_episode
FROM FACT_PATIENT_TASK f
JOIN DIM_PATIENT_TASK_DETAIL d ON f.NK_PATIENT_TASK_KEY = d.NK_PATIENT_TASK_KEY
JOIN DIM_ORGANIZATION o ON f.NK_BRANCH_KEY = o.NK_BRANCH_KEY
WHERE f.IS_DELETED = FALSE
  AND d.IS_DELETED = FALSE
  AND o.NK_CLINIC_KEY = %(clinic_key)s
  AND o.CLINIC_TYPE = 'AGENCY MANAGER'
  AND d.IS_COMPLETED = TRUE
  AND f.IS_MISSED_VISIT = FALSE
  AND d.TASK_TYPE_ABBREVIATION IN ('SN','PT','OT','ST','MSW','LPN/LVN','PTA','COTA','HHA')
  AND d.VISIT_CATEGORY IN ('ADMISSION/ EVAL','RECERTIFICATION/ REEVAL','ROUTINE VISIT','DISCHARGE')
  AND f.NK_EPISODE_KEY IN (
      SELECT DISTINCT f2.NK_EPISODE_KEY
      FROM FACT_PATIENT_TASK f2
      JOIN DIM_PATIENT_TASK_DETAIL d2 ON f2.NK_PATIENT_TASK_KEY = d2.NK_PATIENT_TASK_KEY
      JOIN DIM_ORGANIZATION o2 ON f2.NK_BRANCH_KEY = o2.NK_BRANCH_KEY
      WHERE d2.VISIT_CATEGORY = 'DISCHARGE'
        AND d2.IS_COMPLETED = TRUE
        AND f2.VISIT_DATE BETWEEN %(pp_start)s AND %(pp_end)s
        AND o2.NK_CLINIC_KEY = %(clinic_key)s
  )
"""

SQL_SOC_MEDICARE = """
SELECT
    COUNT(*) AS total_socs,
    SUM(CASE WHEN i.INSURANCE_TYPE ILIKE '%%medicare%%' THEN 1 ELSE 0 END) AS medicare_socs,
    ROUND(SUM(CASE WHEN i.INSURANCE_TYPE ILIKE '%%medicare%%' THEN 1 ELSE 0 END)::FLOAT
          / NULLIF(COUNT(*), 0) * 100, 1) AS medicare_pct
FROM FACT_PATIENT_TASK f
JOIN DIM_PATIENT_TASK_DETAIL d ON f.NK_PATIENT_TASK_KEY = d.NK_PATIENT_TASK_KEY
JOIN DIM_INSURANCE i ON f.NK_INSURANCE_KEY = i.NK_INSURANCE_KEY
JOIN DIM_ORGANIZATION o ON f.NK_BRANCH_KEY = o.NK_BRANCH_KEY
WHERE f.IS_DELETED = FALSE
  AND d.IS_DELETED = FALSE
  AND o.NK_CLINIC_KEY = %(clinic_key)s
  AND o.CLINIC_TYPE = 'AGENCY MANAGER'
  AND d.VISIT_CATEGORY = 'ADMISSION/ EVAL'
  AND d.IS_COMPLETED = TRUE
  AND f.IS_MISSED_VISIT = FALSE
  AND f.VISIT_DATE BETWEEN %(pp_start)s AND %(pp_end)s
  AND f.VISIT_DATE <= CURRENT_DATE()
"""

SQL_ASSISTANT_UTIL = """
SELECT
    CASE WHEN u.USER_TYPE IN ('PTA','COTA','LPN/LVN','HHA')
         THEN 'ASSISTANT' ELSE 'SKILLED' END AS skill_level,
    COUNT(f.NK_PATIENT_TASK_KEY) AS visit_count
FROM FACT_PATIENT_TASK f
JOIN DIM_PATIENT_TASK_DETAIL d ON f.NK_PATIENT_TASK_KEY = d.NK_PATIENT_TASK_KEY
JOIN DIM_USER u ON f.NK_CLINICIAN_USERS_KEY = u.NK_USERS_KEY
JOIN DIM_ORGANIZATION o ON f.NK_BRANCH_KEY = o.NK_BRANCH_KEY
WHERE f.IS_DELETED = FALSE
  AND d.IS_DELETED = FALSE
  AND o.NK_CLINIC_KEY = %(clinic_key)s
  AND o.CLINIC_TYPE = 'AGENCY MANAGER'
  AND f.VISIT_DATE BETWEEN %(pp_start)s AND %(pp_end)s
  AND f.VISIT_DATE <= CURRENT_DATE()
  AND d.IS_COMPLETED = TRUE
  AND f.IS_MISSED_VISIT = FALSE
  AND d.TASK_TYPE_ABBREVIATION IN ('SN','PT','OT','ST','MSW','LPN/LVN','PTA','COTA','HHA')
  AND d.VISIT_CATEGORY IN ('ADMISSION/ EVAL','RECERTIFICATION/ REEVAL','ROUTINE VISIT','DISCHARGE')
GROUP BY skill_level
"""


class SnowflakeClient:
    """Read-only Snowflake connection for QPi scorecard data."""

    def __init__(self, config: dict):
        self.config = config
        self.conn = None

    def connect(self):
        """Establish Snowflake connection (key pair or password)."""
        import snowflake.connector

        connect_args = {
            "account": self.config["account"],
            "user": self.config["user"],
            "role": self.config["role"],
            "warehouse": self.config["warehouse"],
            "database": self.config["database"],
            "schema": self.config["schema"],
        }

        # Key pair auth (preferred)
        if self.config.get("private_key_path"):
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend

            with open(self.config["private_key_path"], "rb") as key_file:
                p_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=None,
                    backend=default_backend(),
                )
            pkb = p_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            connect_args["private_key"] = pkb
            logger.info("Connecting to Snowflake with key pair auth...")
        elif self.config.get("password"):
            connect_args["password"] = self.config["password"]
            logger.info("Connecting to Snowflake with password auth...")
        else:
            raise ValueError("No Snowflake auth configured. Set SNOWFLAKE_PRIVATE_KEY_PATH or SNOWFLAKE_PASSWORD.")

        self.conn = snowflake.connector.connect(**connect_args)
        
        # Explicitly set role after connecting (important when using ADMIN account)
        role = self.config.get("role", "READER")
        cur = self.conn.cursor()
        try:
            cur.execute(f"USE ROLE {role}")
            cur.execute(f"USE WAREHOUSE {self.config['warehouse']}")
            cur.execute(f"USE DATABASE {self.config['database']}")
            cur.execute(f"USE SCHEMA {self.config['schema']}")
        finally:
            cur.close()
        
        logger.info("Snowflake connection established.")
        return self

    def close(self):
        if self.conn:
            self.conn.close()
            logger.info("Snowflake connection closed.")

    def _query(self, sql: str, params: dict) -> pd.DataFrame:
        """Execute query and return DataFrame."""
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            cols = [desc[0].lower() for desc in cur.description]
            rows = cur.fetchall()
            return pd.DataFrame(rows, columns=cols)
        finally:
            cur.close()

    def _params(self, clinic_key: int, pp_start: date, pp_end: date) -> dict:
        return {
            "clinic_key": clinic_key,
            "pp_start": pp_start.isoformat(),
            "pp_end": pp_end.isoformat(),
        }

    # -----------------------------------------------------------------
    # Public query methods — each returns a DataFrame
    # -----------------------------------------------------------------

    def get_clinician_productivity(self, clinic_key: int, pp_start: date, pp_end: date) -> pd.DataFrame:
        logger.info(f"Querying clinician productivity: {pp_start} to {pp_end}")
        return self._query(SQL_CLINICIAN_PRODUCTIVITY, self._params(clinic_key, pp_start, pp_end))

    def get_documentation(self, clinic_key: int, pp_start: date, pp_end: date) -> pd.DataFrame:
        logger.info(f"Querying documentation timeliness: {pp_start} to {pp_end}")
        return self._query(SQL_DOCUMENTATION, self._params(clinic_key, pp_start, pp_end))

    def get_census(self, clinic_key: int, pp_start: date, pp_end: date) -> pd.DataFrame:
        return self._query(SQL_CENSUS, self._params(clinic_key, pp_start, pp_end))

    def get_lupa(self, clinic_key: int, pp_start: date, pp_end: date) -> pd.DataFrame:
        return self._query(SQL_LUPA, self._params(clinic_key, pp_start, pp_end))

    def get_non_admits(self, clinic_key: int, pp_start: date, pp_end: date) -> pd.DataFrame:
        return self._query(SQL_NON_ADMITS, self._params(clinic_key, pp_start, pp_end))

    def get_visits_per_episode(self, clinic_key: int, pp_start: date, pp_end: date) -> pd.DataFrame:
        return self._query(SQL_VISITS_PER_EPISODE, self._params(clinic_key, pp_start, pp_end))

    def get_soc_medicare(self, clinic_key: int, pp_start: date, pp_end: date) -> pd.DataFrame:
        return self._query(SQL_SOC_MEDICARE, self._params(clinic_key, pp_start, pp_end))

    def get_assistant_utilization(self, clinic_key: int, pp_start: date, pp_end: date) -> pd.DataFrame:
        return self._query(SQL_ASSISTANT_UTIL, self._params(clinic_key, pp_start, pp_end))

    def get_all(self, clinic_key: int, pp_start: date, pp_end: date) -> dict:
        """Run all queries and return dict of DataFrames."""
        return {
            "productivity": self.get_clinician_productivity(clinic_key, pp_start, pp_end),
            "documentation": self.get_documentation(clinic_key, pp_start, pp_end),
            "census": self.get_census(clinic_key, pp_start, pp_end),
            "lupa": self.get_lupa(clinic_key, pp_start, pp_end),
            "non_admits": self.get_non_admits(clinic_key, pp_start, pp_end),
            "visits_per_episode": self.get_visits_per_episode(clinic_key, pp_start, pp_end),
            "soc_medicare": self.get_soc_medicare(clinic_key, pp_start, pp_end),
            "assistant_util": self.get_assistant_utilization(clinic_key, pp_start, pp_end),
        }
