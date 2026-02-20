"""
Cap Dashboard — Configuration
American Premier Hospice — Medicare Hospice Cap Tracking

All static parameters live here (no Snowflake writes needed).
PS&R actuals are updated manually when new PS&R reports are pulled.
"""
from datetime import date
from typing import Optional

# =============================================================================
# AGENCY
# =============================================================================
CLINIC_KEY = 8208        # Hospice entity (NOT 3743, which is home health)
BRANCH_KEY = 8883        # Hospice branch
AGENCY_NAME = "American Premier Hospice"
ACQUISITION_DATE = date(2025, 2, 14)

# For reference:
# Clinic 3743 = American Premier Home Health (QPi scorecard)
# Clinic 8208 = American Premier Hospice (cap dashboard)

# =============================================================================
# CAP YEARS
# =============================================================================
# CMS publishes the per-beneficiary cap amount each federal fiscal year (Oct 1 – Sep 30).
# PS&R actuals are updated monthly when the PS&R report is pulled from CMS.

CAP_YEARS = {
    "FY2025": {
        "label": "FY2025 (Oct 2024 – Sep 2025)",
        "start": date(2024, 10, 1),
        "end": date(2025, 9, 30),
        "cap_rate": 34465.34,
        "sequestration_rate": 0.02,
        "psr_actuals": {
            "report_date": "2026-02-17",
            "pro_rated_bene": 73.0393,
            "gross_reimbursement": 2825911.70,
            "sequestration": 56517.03,
            "net_reimbursement": 2769394.67,
        },
    },
    "FY2026": {
        "label": "FY2026 (Oct 2025 – Sep 2026)",
        "start": date(2025, 10, 1),
        "end": date(2026, 9, 30),
        "cap_rate": 35361.44,
        "sequestration_rate": 0.02,
        "psr_actuals": None,  # Updated when PS&R is pulled
    },
}

DEFAULT_CAP_YEAR = "FY2025"


def get_cap_year_for_date(d: date) -> Optional[str]:
    """Return the cap year key (e.g., 'FY2025') that contains the given date."""
    for key, cy in CAP_YEARS.items():
        if cy["start"] <= d <= cy["end"]:
            return key
    return None


def get_current_cap_year() -> str:
    """Return the cap year key for today's date, or the most recent one."""
    today = date.today()
    key = get_cap_year_for_date(today)
    if key:
        return key
    # Fall back to the latest defined cap year
    return max(CAP_YEARS.keys())


# =============================================================================
# RISK TIERING — based on benefit period number
# =============================================================================
# HIGH:    BP >= 5  (long-stay, high cap consumption)
# WATCH:   BP 3-4   (approaching risk)
# HEALTHY: BP 1-2   (normal, low cap exposure)

RISK_TIERS = {
    "HIGH":    {"bp_min": 5, "color": "#FF4B4B", "label": "High Risk"},
    "WATCH":   {"bp_min": 3, "color": "#FFA500", "label": "Watch"},
    "HEALTHY": {"bp_min": 1, "color": "#00CC66", "label": "Healthy"},
}


def get_risk_tier(benefit_period_number: int) -> str:
    """Return risk tier label based on benefit period number."""
    if benefit_period_number >= RISK_TIERS["HIGH"]["bp_min"]:
        return "HIGH"
    elif benefit_period_number >= RISK_TIERS["WATCH"]["bp_min"]:
        return "WATCH"
    return "HEALTHY"


# =============================================================================
# ADMISSIONS MIX TARGETS
# =============================================================================
ADMISSIONS_TARGET_SHORT_STAY_PCT = 0.70   # 70% of admits should be BP 1-2
ADMISSIONS_FLAG_THRESHOLD = 0.60          # Flag if drops below 60%

# =============================================================================
# CAP UTILIZATION ALERTS
# =============================================================================
CAP_ALERT_THRESHOLD = 0.85   # Alert when 85% of allowable is consumed
CAP_DANGER_THRESHOLD = 0.95  # Danger when 95% of allowable is consumed

# =============================================================================
# LEVEL OF CARE CATEGORIES (from FACT_LEVEL_OF_CARE)
# =============================================================================
LOC_CATEGORIES = ["Routine", "Continuous", "Respite", "GIP"]

# =============================================================================
# DISPLAY / FORMATTING
# =============================================================================
CURRENCY_FORMAT = "${:,.2f}"
PCT_FORMAT = "{:.1f}%"
BENE_FORMAT = "{:.4f}"
