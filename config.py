"""
QPi Scorecard Automation — Configuration
American Premier Home Health
"""
from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import Optional
import os


# =============================================================================
# SNOWFLAKE CONNECTION
# =============================================================================
SNOWFLAKE_CONFIG = {
    "account": os.getenv("SF_ACCOUNT", "your_account.snowflakecomputing.com"),
    "user": os.getenv("SF_USER", ""),
    "role": os.getenv("SF_ROLE", "READER"),
    "warehouse": os.getenv("SF_WAREHOUSE", "WH_HEALTH_MAX_GROUP_10026_XSM"),
    "database": "WS_HHH_BI_DW_READONLY",
    "schema": "WS",
    # Auth: key pair (preferred) or password
    "private_key_path": os.getenv("SF_PRIVATE_KEY_PATH", ""),
    "password": os.getenv("SF_PASSWORD", ""),
}


# =============================================================================
# iSOLVED CONFIG (flexible — CSV, direct API, or Finch)
# =============================================================================
ISOLVED_MODE = os.getenv("ISOLVED_MODE", "csv")  # "csv", "api", "finch"

ISOLVED_CSV_DIR = os.getenv("ISOLVED_CSV_DIR", "./input/isolved")

ISOLVED_API_CONFIG = {
    "base_url": os.getenv("ISOLVED_API_URL", ""),
    "client_id": os.getenv("ISOLVED_CLIENT_ID", ""),
    "client_secret": os.getenv("ISOLVED_CLIENT_SECRET", ""),
    "company_id": os.getenv("ISOLVED_COMPANY_ID", ""),
}

FINCH_API_CONFIG = {
    "base_url": "https://api.tryfinch.com",
    "access_token": os.getenv("FINCH_ACCESS_TOKEN", ""),
}


# =============================================================================
# AGENCY DEFINITIONS
# =============================================================================
@dataclass
class Agency:
    name: str
    clinic_key: int
    short_code: str
    region: str
    is_ppv: bool = False  # pay-per-visit region


AGENCY = Agency("American Premier Home Health", 3743, "AP", "AZ", is_ppv=True)
CLINIC_KEY = AGENCY.clinic_key


# =============================================================================
# PAY PERIOD CALENDAR
# =============================================================================
PP_ANCHOR_START = date(2026, 1, 25)  # Known PP4 start (Sunday)
PP_ANCHOR_NUMBER = 4

STANDARD_HOURS = 80.0
STANDARD_POINTS_TARGET = 30.0


def get_pay_period(pp_number: Optional[int] = None) -> dict:
    """
    Calculate pay period dates from anchor.
    pp_number=None -> most recent completed period.
    """
    today = date.today()

    if pp_number is not None and pp_number > 0:
        # Manual: specific period
        delta_periods = pp_number - PP_ANCHOR_NUMBER
        start = PP_ANCHOR_START + timedelta(days=delta_periods * 14)
    else:
        # Auto: most recent completed period (end date <= today)
        days_since_anchor = (today - PP_ANCHOR_START).days
        periods_elapsed = days_since_anchor // 14
        candidate_start = PP_ANCHOR_START + timedelta(days=periods_elapsed * 14)
        candidate_end = candidate_start + timedelta(days=13)

        if candidate_end > today:
            # Current period not finished — go back one
            candidate_start -= timedelta(days=14)

        start = candidate_start

    end = start + timedelta(days=13)
    pp_num = PP_ANCHOR_NUMBER + (start - PP_ANCHOR_START).days // 14

    return {
        "start": start,
        "end": end,
        "number": pp_num,
        "label": f"PP{pp_num}",
        "range_str": f"{start.strftime('%m/%d/%Y')} – {end.strftime('%m/%d/%Y')}",
    }


def get_prior_period(current_pp: dict) -> dict:
    """Get the pay period immediately before the given one."""
    return get_pay_period(current_pp["number"] - 1)


# =============================================================================
# POINT SYSTEM
# =============================================================================
VISIT_POINTS = {
    "ADMISSION/ EVAL": 2.5,       # SOC/Eval (default to SOC rate)
    "RECERTIFICATION/ REEVAL": 1.5,
    "ROUTINE VISIT": 1.0,
    "DISCHARGE": 1.0,
}

BEST_PRACTICE_HOURS = {
    "ADMISSION/ EVAL": 2.75,      # SOC/ROC
    "RECERTIFICATION/ REEVAL": 1.75,
    "ROUTINE VISIT": 1.25,
    "DISCHARGE": 1.25,
}

# PTO defaults for point credit
PTO_POINTS_FULL_DAY = 6.0
PTO_POINTS_HALF_DAY = 3.0

# OPP bonus
OPP_AMOUNT = 50.00
OPP_REQUIRES_POINTS = True
OPP_REQUIRES_UTILIZATION = True


# =============================================================================
# KPI TARGETS
# =============================================================================
TARGETS = {
    "star_rating": {"value": 4.0, "direction": "gte", "label": "Star Rating", "source": "SHP"},
    "nps": {"value": 90.0, "direction": "gt", "label": "Patient Satisfaction (NPS)", "source": "Hippocratic AI"},
    "doc_on_time_pct": {"value": 90.0, "direction": "gt", "label": "Documentation On-Time %", "source": "Snowflake"},
    "doc_accuracy_pct": {"value": 100.0, "direction": "gte", "label": "Documentation Accuracy %", "source": "Snowflake"},
    "utilization_pct": {"value": 100.0, "direction": "gte", "label": "Clinician Utilization %", "source": "Calculated"},
    "missed_visit_pct": {"value": 5.0, "direction": "lt", "label": "Missed Visits %", "source": "Snowflake"},
    "ft_capacity_pct": {"value": 80.0, "direction": "gt", "label": "FT Clinician Capacity", "source": "iSolved"},
    "overtime_hrs": {"value": 20.0, "direction": "lt", "label": "Clinician Overtime", "source": "iSolved"},
    "assistant_pct": {"value": 40.0, "direction": "gt", "label": "Clinician Assistants %", "source": "Snowflake"},
    "cpv": {"value": 105.0, "direction": "lt", "label": "Contractor CPV", "source": "Calculated"},
    "lupa_pct": {"value": 7.0, "direction": "lte", "label": "LUPA %", "source": "Snowflake"},
    "visits_per_episode": {"value": 8.5, "direction": "lte", "label": "Visits/Episode", "source": "Snowflake"},
    "census": {"value": 125, "direction": "gt", "label": "Census", "source": "Snowflake"},
    "non_admits": {"value": 3, "direction": "lte", "label": "Non-Admits", "source": "Snowflake"},
    "soc_medicare_pct": {"value": 80.0, "direction": "gt", "label": "SoC Medicare %", "source": "Snowflake"},
    "unbilled_pct": {"value": 10.0, "direction": "lte", "label": "Unbilled %", "source": "Billing"},
    "gross_margin": {"value": 45.0, "direction": "gt", "label": "Gross Margin %", "source": "Financial"},
    "sga_pct": {"value": 27.0, "direction": "lt", "label": "SG&A Expenses %", "source": "Financial"},
    "ebitda": {"value": 17.0, "direction": "gt", "label": "EBITDA %", "source": "Financial"},
    "points_per_hour": {"value": 0.60, "direction": "gte", "label": "Points/Hour", "source": "Calculated"},
}


# Assistant disciplines (for utilization %)
ASSISTANT_DISCIPLINES = {"PTA", "COTA", "LPN/LVN", "HHA"}
SKILLED_DISCIPLINES = {"SN", "RN", "PT", "OT", "ST", "MSW"}

# Clinical task abbreviations included in scorecard
CLINICAL_TASK_ABBREVIATIONS = [
    "SN", "PT", "OT", "ST", "MSW", "LPN/LVN", "PTA", "COTA", "HHA"
]

SCORED_VISIT_CATEGORIES = [
    "ADMISSION/ EVAL", "RECERTIFICATION/ REEVAL", "ROUTINE VISIT", "DISCHARGE"
]


# =============================================================================
# REPORT VERSION
# =============================================================================
REPORT_VERSION = "1.0"

# =============================================================================
# OUTPUT
# =============================================================================
OUTPUT_DIR = os.getenv("QPi_OUTPUT_DIR", "./output")
INPUT_DIR = os.getenv("QPi_INPUT_DIR", "./input")


# =============================================================================
# DISTRIBUTION (Phase 4)
# =============================================================================
EMAIL_CONFIG = {
    "smtp_server": os.getenv("SMTP_SERVER", ""),
    "smtp_port": int(os.getenv("SMTP_PORT", "587")),
    "smtp_user": os.getenv("SMTP_USER", ""),
    "smtp_pass": os.getenv("SMTP_PASS", ""),
    "from_address": os.getenv("QPi_FROM_EMAIL", "qpi@americanpremierhh.com"),
}
