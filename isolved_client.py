"""
QPi Scorecard Automation — iSolved Payroll Adapter
Pluggable adapter: CSV (now), iSolved API (later), Finch (later).
All adapters return the same standardized DataFrame.
"""
import logging
import os
import re
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# =============================================================================
# STANDARDIZED OUTPUT SCHEMA
# =============================================================================
# Every adapter must return a DataFrame with these columns:
PAYROLL_SCHEMA = {
    "clinician_name": str,    # "DAVID BERRY"
    "associate_id": str,      # iSolved employee ID
    "agency": str,            # Agency name
    "discipline": str,        # "PT", "RN", etc. (if available)
    "pay_type": str,          # "Salary", "Hourly", "PPV"
    "status": str,            # "Full Time", "Part Time", "PRN"
    "regular_hours": float,   # Standard hours worked
    "overtime_hours": float,  # OT hours
    "pay_rate": float,        # Hourly or per-period rate
    "gross_wages": float,     # Total gross wages
    "mileage": float,         # Mileage reimbursement
    "total_cost": float,      # gross_wages + mileage
    "vacation_hours": float,
    "pto_hours": float,
    "sick_hours": float,
    "holiday_hours": float,
    "bereavement_hours": float,
    "time_off_hours": float,  # Sum of all time off
    "on_call_weekday": int,   # Count of weekday on-call days
    "on_call_weekend": int,   # Count of weekend on-call days
    "on_call_pay": float,     # Total on-call pay
    # Visit counts from iSolved earnings codes (PPV regions)
    "isolved_soc_ct": int,
    "isolved_roc_ct": int,
    "isolved_eval_ct": int,
    "isolved_visit_ct": int,
}


class PayrollAdapter(ABC):
    """Base class for payroll data adapters."""

    @abstractmethod
    def get_payroll(self, pp_start: date, pp_end: date, agency: Optional[str] = None) -> pd.DataFrame:
        """Return standardized payroll DataFrame for the given pay period."""
        pass


# =============================================================================
# CSV ADAPTER (works today)
# =============================================================================
class CSVPayrollAdapter(PayrollAdapter):
    """
    Reads iSolved Payroll Register CSV exports.
    Expects files in a directory, named with pay period dates.
    e.g., isolved_20260125_20260207.csv or any CSV in the folder.
    """

    # Earnings code mapping (from iSolved payroll register analysis)
    EARNINGS_MAP = {
        # Wages
        "Regular": "regular",
        "Salary": "salary",
        "Overtime": "overtime",
        # Visit-based pay
        "HH SOC": "soc",
        "HH SOC On Time": "soc_bonus",
        "HH SOC Late": "soc_late",
        "HH ROC": "roc",
        "OT Eval": "eval",
        "HH Regular Visit": "visit",
        "HH Rgular Visit": "visit",  # typo in iSolved
        "HH LPN Visit": "lpn_visit",
        "HH PTA Visit": "pta_visit",
        # Time off
        "Vacation": "vacation",
        "PTO": "pto",
        "Sick": "sick",
        "Holiday": "holiday",
        "Bereavement": "bereavement",
        # On-call
        "On Call Weekday": "oncall_wd",
        "On Call Weekend": "oncall_we",
        # Other
        "Mileage": "mileage",
        "Meeting": "meeting",
    }

    def __init__(self, csv_dir: str):
        self.csv_dir = Path(csv_dir)

    def _find_csv(self, pp_start: date, pp_end: date) -> Optional[Path]:
        """Find the CSV file matching the pay period."""
        if not self.csv_dir.exists():
            logger.warning(f"CSV directory not found: {self.csv_dir}")
            return None

        # Try matching by date in filename
        start_str = pp_start.strftime("%Y%m%d")
        end_str = pp_end.strftime("%Y%m%d")

        for f in self.csv_dir.glob("*.csv"):
            if start_str in f.name or end_str in f.name:
                return f

        # Fallback: return most recent CSV
        csvs = sorted(self.csv_dir.glob("*.csv"), key=os.path.getmtime, reverse=True)
        if csvs:
            logger.warning(f"No date-matched CSV found, using most recent: {csvs[0].name}")
            return csvs[0]

        return None

    def _parse_payroll_register(self, filepath: Path) -> pd.DataFrame:
        """
        Parse iSolved Payroll Register export.
        Handles multiple iSolved report formats:
        - Employee Earnings Summary (Gross Wage, 1099/Exp Reimburse, etc.)
        - Detailed Payroll Register (hours, earnings codes)
        - Standard tabular CSV with recognizable column names
        """
        logger.info(f"Parsing iSolved CSV: {filepath}")

        # Try standard CSV parse first
        try:
            raw = pd.read_csv(filepath, thousands=",")
        except Exception:
            # iSolved PDFs converted to CSV may have irregular format
            # Fall back to line-by-line parsing
            return self._parse_payroll_register_manual(filepath)

        # Strip whitespace from column names
        raw.columns = raw.columns.str.strip()

        # Drop "Report Totals" rows
        for col in raw.columns:
            if raw[col].dtype == object:
                raw = raw[~raw[col].astype(str).str.contains("Report Totals", na=False)]
                break

        # Detect format by column names
        cols_lower = {c.lower().strip() for c in raw.columns}
        has_name_col = any(
            "name" in c or "employee" in c
            for c in cols_lower
        )

        if has_name_col:
            return self._normalize_standard_csv(raw)

        # Otherwise try manual parsing
        return self._parse_payroll_register_manual(filepath)

    # Column name mapping: iSolved column name (lowercase) -> standardized name
    # Uses substring matching: (pattern, secondary_pattern_or_None, target)
    # If secondary is set, BOTH must match. If None, only primary must match.
    COLUMN_PATTERNS = [
        # Identity — handle "Employee Name", "Name", "Employee", etc.
        ("employee", "name", "clinician_name"),
        # Hours
        ("regular", "hour", "regular_hours"),
        ("overtime", "hour", "overtime_hours"),
        ("ot_hour", None, "overtime_hours"),
        ("ot hour", None, "overtime_hours"),
        # Pay
        ("pay_rate", None, "pay_rate"),
        ("pay rate", None, "pay_rate"),
        ("mileage", None, "mileage"),
        # Time off
        ("vacation", None, "vacation_hours"),
        ("pto", None, "pto_hours"),
        ("sick", None, "sick_hours"),
        ("holiday", None, "holiday_hours"),
        ("bereavement", None, "bereavement_hours"),
        # On call
        ("on_call_weekday", None, "on_call_weekday"),
        ("on call weekday", None, "on_call_weekday"),
        ("on_call_weekend", None, "on_call_weekend"),
        ("on call weekend", None, "on_call_weekend"),
        ("on_call_pay", None, "on_call_pay"),
        ("on call pay", None, "on_call_pay"),
        # Earnings-code visit counts (from iSolved)
        ("hh soc", None, "isolved_soc_ct"),
        ("hh roc", None, "isolved_roc_ct"),
        ("ot eval", None, "isolved_eval_ct"),
        ("hh regular visit", None, "isolved_visit_ct"),
        ("hh rgular visit", None, "isolved_visit_ct"),  # iSolved typo
        ("hh lpn visit", None, "isolved_lpn_visit_ct"),
        ("hh pta visit", None, "isolved_pta_visit_ct"),
    ]

    # Exact column name overrides for Employee Earnings Summary format
    EXACT_COLUMN_MAP = {
        "emp #": "associate_id",
        "employee name": "clinician_name",
        "name": "clinician_name",
        "gross wage": "gross_wages",
        "gross wages": "gross_wages",
        "1099/exp reimburse": "mileage",
        "paid earnings": "_paid_earnings",  # duplicate of gross, ignored
        "agency": "agency",
        "discipline": "discipline",
        "pay_type": "pay_type",
        "pay type": "pay_type",
        "status": "status",
        "associate": "associate_id",
        "emp_id": "associate_id",
        "employee_id": "associate_id",
        "gross": "gross_wages",
    }

    def _normalize_standard_csv(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize a standard tabular CSV export from any iSolved report format."""
        col_map = {}
        mapped_targets = set()

        # Pass 1: exact column name match (handles iSolved-specific headers)
        for col in df.columns:
            cl = col.lower().strip()
            if cl in self.EXACT_COLUMN_MAP:
                target = self.EXACT_COLUMN_MAP[cl]
                if target not in mapped_targets:
                    col_map[col] = target
                    mapped_targets.add(target)

        # Pass 2: substring pattern match for remaining columns
        for col in df.columns:
            if col in col_map:
                continue
            cl = col.lower().strip()
            for primary, secondary, target in self.COLUMN_PATTERNS:
                if target in mapped_targets:
                    continue
                if secondary:
                    if primary in cl and secondary in cl:
                        col_map[col] = target
                        mapped_targets.add(target)
                        break
                else:
                    if cl == primary or cl.replace(" ", "_") == primary.replace(" ", "_"):
                        col_map[col] = target
                        mapped_targets.add(target)
                        break

        df = df.rename(columns=col_map)

        # Drop internal-only columns
        df.drop(columns=["_paid_earnings"], inplace=True, errors="ignore")

        # Strip commas from numeric strings and convert
        for col in df.columns:
            if col == "clinician_name" or col == "associate_id":
                continue
            if df[col].dtype == object:
                df[col] = df[col].astype(str).str.replace(",", "", regex=False)
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Convert "Last, First Middle" -> "FIRST LAST" for Snowflake merge
        if "clinician_name" in df.columns:
            df["clinician_name"] = df["clinician_name"].apply(self._normalize_name)

        # Filter out zero-wage inactive employees
        if "gross_wages" in df.columns:
            before = len(df)
            df = df[df["gross_wages"] > 0].copy()
            dropped = before - len(df)
            if dropped:
                logger.info(f"Filtered {dropped} zero-wage employees (inactive)")

        # Sum assistant visit columns into isolved_visit_ct if present
        for aux_col in ["isolved_lpn_visit_ct", "isolved_pta_visit_ct"]:
            if aux_col in df.columns:
                df[aux_col] = pd.to_numeric(df[aux_col], errors="coerce").fillna(0)
                df["isolved_visit_ct"] = df.get("isolved_visit_ct", 0) + df[aux_col]
                df.drop(columns=[aux_col], inplace=True)

        df = self._fill_defaults(df)
        df = self._compute_derived(df)

        logger.info(f"Parsed {len(df)} employees from iSolved CSV")
        for _, row in df.iterrows():
            logger.debug(f"  {row['clinician_name']}: gross={row['gross_wages']}, "
                         f"mileage={row['mileage']}, total_cost={row['total_cost']}")

        return df

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Convert 'Last, First Middle' to 'FIRST LAST' (uppercase) for Snowflake merge."""
        if pd.isna(name) or not name:
            return ""
        name = str(name).strip().upper()
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            first_parts = parts[1].strip().split()
            # Take just the first name (drop middle initial/name)
            first = first_parts[0] if first_parts else ""
            return f"{first} {last}"
        return name

    def _compute_derived(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute total_cost and time_off_hours from component columns."""
        for col in list(PAYROLL_SCHEMA.keys()):
            if col in df.columns and PAYROLL_SCHEMA[col] in (float, int):
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        df["time_off_hours"] = (
            df["vacation_hours"] + df["pto_hours"] + df["sick_hours"]
            + df["holiday_hours"] + df["bereavement_hours"]
        )
        df["total_cost"] = df["gross_wages"] + df["mileage"]
        return df

    def _parse_payroll_register_manual(self, filepath: Path) -> pd.DataFrame:
        """
        Manual line-by-line parser for iSolved Payroll Register format.
        This handles the grouped-by-employee format with earnings detail rows.
        """
        logger.info("Using manual parser for payroll register format")

        employees = []
        current_emp = None

        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                # Detect new employee header (name pattern)
                # iSolved format: "LastName, FirstName  EmployeeID  Agency  Status"
                # This is heuristic — adjust based on actual format
                if self._is_employee_header(line):
                    if current_emp:
                        employees.append(self._finalize_employee(current_emp))
                    current_emp = self._init_employee(line)
                elif current_emp and self._is_earnings_line(line):
                    self._parse_earnings_line(current_emp, line)

        if current_emp:
            employees.append(self._finalize_employee(current_emp))

        if not employees:
            logger.warning("No employees parsed from payroll register")
            return pd.DataFrame(columns=list(PAYROLL_SCHEMA.keys()))

        return pd.DataFrame(employees)

    def _is_employee_header(self, line: str) -> bool:
        """Heuristic: detect employee header lines."""
        # Override this based on your actual iSolved export format
        return False

    def _is_earnings_line(self, line: str) -> bool:
        """Heuristic: detect earnings detail lines."""
        return False

    def _init_employee(self, line: str) -> dict:
        return {k: 0 if v in (float, int) else "" for k, v in PAYROLL_SCHEMA.items()}

    def _finalize_employee(self, emp: dict) -> dict:
        emp["time_off_hours"] = (
            emp["vacation_hours"] + emp["pto_hours"] + emp["sick_hours"]
            + emp["holiday_hours"] + emp["bereavement_hours"]
        )
        emp["total_cost"] = emp["gross_wages"] + emp["mileage"]
        return emp

    def _parse_earnings_line(self, emp: dict, line: str):
        pass

    def _fill_defaults(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure all schema columns exist with defaults."""
        for col, dtype in PAYROLL_SCHEMA.items():
            if col not in df.columns:
                df[col] = 0 if dtype in (float, int) else ""
        return df

    def get_payroll(self, pp_start: date, pp_end: date, agency: Optional[str] = None) -> pd.DataFrame:
        filepath = self._find_csv(pp_start, pp_end)
        if filepath is None:
            logger.warning("No iSolved CSV found — returning empty DataFrame")
            return pd.DataFrame(columns=list(PAYROLL_SCHEMA.keys()))

        df = self._parse_payroll_register(filepath)

        if agency and "agency" in df.columns:
            df = df[df["agency"].str.contains(agency, case=False, na=False)]

        return df


# =============================================================================
# iSOLVED API ADAPTER (future)
# =============================================================================
class IsolvedAPIAdapter(PayrollAdapter):
    """
    Direct iSolved API integration.
    Requires: API credentials from iSolved.
    Docs: https://developer.isolved.com/
    """

    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "")
        self.client_id = config.get("client_id", "")
        self.client_secret = config.get("client_secret", "")
        self.company_id = config.get("company_id", "")
        self._token = None

    def _authenticate(self):
        """Get OAuth2 bearer token from iSolved."""
        import requests

        resp = requests.post(
            f"{self.base_url}/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        logger.info("iSolved API authenticated.")

    def _get(self, endpoint: str, params: dict = None) -> dict:
        """Authenticated GET request."""
        import requests

        if not self._token:
            self._authenticate()

        resp = requests.get(
            f"{self.base_url}/{endpoint}",
            headers={"Authorization": f"Bearer {self._token}"},
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    def get_payroll(self, pp_start: date, pp_end: date, agency: Optional[str] = None) -> pd.DataFrame:
        """
        Pull payroll register data via iSolved API.
        Endpoint and field mapping TBD based on iSolved API docs.
        """
        logger.info(f"Pulling iSolved API payroll: {pp_start} to {pp_end}")

        # TODO: Implement once API credentials are available
        # Expected flow:
        # 1. GET /payroll/registers?startDate=...&endDate=...&companyId=...
        # 2. Parse response into standardized schema
        # 3. Map earnings codes to visit types

        raise NotImplementedError(
            "iSolved API adapter not yet configured. "
            "Set ISOLVED_MODE=csv and provide CSV exports, "
            "or configure API credentials in environment variables."
        )


# =============================================================================
# FINCH ADAPTER (future)
# =============================================================================
class FinchPayrollAdapter(PayrollAdapter):
    """
    Finch unified payroll API (connects to iSolved and 200+ providers).
    Docs: https://developer.tryfinch.com/
    """

    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "https://api.tryfinch.com")
        self.access_token = config.get("access_token", "")

    def _get(self, endpoint: str) -> dict:
        import requests

        resp = requests.get(
            f"{self.base_url}/{endpoint}",
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_payroll(self, pp_start: date, pp_end: date, agency: Optional[str] = None) -> pd.DataFrame:
        """
        Pull payroll via Finch API.
        Finch normalizes across providers so field mapping is simpler.
        """
        logger.info(f"Pulling Finch payroll: {pp_start} to {pp_end}")

        # TODO: Implement once Finch access token is available
        # Expected flow:
        # 1. POST /employer/pay-statements with start/end dates
        # 2. Parse normalized response
        # 3. Map to QPi schema

        raise NotImplementedError(
            "Finch adapter not yet configured. "
            "Set ISOLVED_MODE=csv or configure FINCH_ACCESS_TOKEN."
        )


# =============================================================================
# FACTORY
# =============================================================================
def get_payroll_adapter(mode: str, **kwargs) -> PayrollAdapter:
    """Factory: returns the correct adapter based on config."""
    if mode == "csv":
        return CSVPayrollAdapter(csv_dir=kwargs.get("csv_dir", "./input/isolved"))
    elif mode == "api":
        return IsolvedAPIAdapter(config=kwargs.get("api_config", {}))
    elif mode == "finch":
        return FinchPayrollAdapter(config=kwargs.get("finch_config", {}))
    else:
        raise ValueError(f"Unknown iSolved mode: {mode}. Use 'csv', 'api', or 'finch'.")
