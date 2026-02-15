"""
QPi Scorecard Automation — Main Orchestrator
Usage:
    python main.py                        # Auto-detect most recent completed pay period
    python main.py --pp 4                 # Run for specific pay period
    python main.py --pp 4 --agency 3743   # Single agency
    python main.py --pp 4 --all-agencies  # All agencies
    python main.py --pp 4 --trend         # Include prior period comparison
    python main.py --pp 4 --trend --log-file output/qpi.log  # With log file
"""
import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

# Load .env before importing config
from load_env import load_dotenv
load_dotenv()

import pandas as pd

from config import (
    SNOWFLAKE_CONFIG, ISOLVED_MODE, ISOLVED_CSV_DIR,
    ISOLVED_API_CONFIG, FINCH_API_CONFIG,
    AGENCIES, DEFAULT_CLINIC_KEY,
    OUTPUT_DIR, INPUT_DIR,
    get_pay_period, get_prior_period,
)
from snowflake_client import SnowflakeClient
from isolved_client import get_payroll_adapter
from scorecard_engine import build_clinician_scorecard, build_agency_metrics
from excel_builder import build_workbook

# =============================================================================
# LOGGING
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("QPi")


def _validate_env() -> None:
    """Pre-flight: ensure required Snowflake env vars are set. Exit 1 if not."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        logger.error("No .env file found. Copy .env.template to .env and fill in values.")
        sys.exit(1)

    account = os.getenv("SF_ACCOUNT", "").strip()
    user = os.getenv("SF_USER", "").strip()
    password = os.getenv("SF_PASSWORD", "").strip()
    key_path = os.getenv("SF_PRIVATE_KEY_PATH", "").strip()

    if not account:
        logger.error("SF_ACCOUNT is not set in .env")
        sys.exit(1)
    if not user:
        logger.error("SF_USER is not set in .env")
        sys.exit(1)
    if not password and not key_path:
        logger.error("Set either SF_PASSWORD or SF_PRIVATE_KEY_PATH in .env")
        sys.exit(1)


def run_scorecard(
    clinic_key: int,
    pp_number: int = None,
    include_trend: bool = False,
):
    """
    Run the full QPi scorecard pipeline for one agency and one pay period.

    Steps:
    1. Calculate pay period dates
    2. Connect to Snowflake and pull all data
    3. Pull iSolved payroll data
    4. Merge and calculate all KPIs
    5. Generate Excel workbook
    6. (Optional) Pull prior period for trending
    """
    agency = AGENCIES.get(clinic_key)
    if not agency:
        logger.error(f"Unknown clinic_key: {clinic_key}")
        return None

    # ------------------------------------------------------------------
    # Step 1: Pay period
    # ------------------------------------------------------------------
    pp = get_pay_period(pp_number)
    logger.info(f"{'='*60}")
    logger.info(f"QPi SCORECARD: {agency.name}")
    logger.info(f"Pay Period: {pp['label']} ({pp['range_str']})")
    logger.info(f"{'='*60}")

    # ------------------------------------------------------------------
    # Step 2: Snowflake data
    # ------------------------------------------------------------------
    sf = SnowflakeClient(SNOWFLAKE_CONFIG)
    try:
        sf.connect()
        sf_data = sf.get_all(clinic_key, pp["start"], pp["end"])

        # Prior period (for trending)
        prior_prod = None
        prior_docs = None
        if include_trend:
            prior_pp = get_prior_period(pp)
            logger.info(f"Pulling prior period: {prior_pp['label']} ({prior_pp['range_str']})")
            prior_prod = sf.get_clinician_productivity(clinic_key, prior_pp["start"], prior_pp["end"])
            prior_docs = sf.get_documentation(clinic_key, prior_pp["start"], prior_pp["end"])
    finally:
        sf.close()

    logger.info(f"Snowflake: {len(sf_data['productivity'])} clinicians, "
                f"census={sf_data['census'].iloc[0]['census_count'] if not sf_data['census'].empty else 'N/A'}")

    # ------------------------------------------------------------------
    # Step 3: iSolved payroll
    # ------------------------------------------------------------------
    payroll_adapter = get_payroll_adapter(
        mode=ISOLVED_MODE,
        csv_dir=ISOLVED_CSV_DIR,
        api_config=ISOLVED_API_CONFIG,
        finch_config=FINCH_API_CONFIG,
    )

    try:
        payroll_df = payroll_adapter.get_payroll(pp["start"], pp["end"], agency.name)
        logger.info(f"iSolved: {len(payroll_df)} employees loaded")
    except NotImplementedError as e:
        logger.warning(f"iSolved adapter not available: {e}")
        payroll_df = pd.DataFrame()
    except Exception as e:
        logger.error(f"iSolved error: {e}")
        payroll_df = pd.DataFrame()

    # ------------------------------------------------------------------
    # Step 4: Build scorecard
    # ------------------------------------------------------------------
    clinician_df = build_clinician_scorecard(
        sf_productivity=sf_data["productivity"],
        sf_documentation=sf_data["documentation"],
        payroll=payroll_df,
        prior_productivity=prior_prod if include_trend else None,
        prior_documentation=prior_docs if include_trend else None,
    )

    agency_metrics = build_agency_metrics(sf_data)

    # ------------------------------------------------------------------
    # Step 5: Generate outputs
    # ------------------------------------------------------------------
    output_dir = Path(OUTPUT_DIR) / f"{pp['label']}_{agency.short_code}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Excel workbook
    xlsx_path = output_dir / f"QPi_Scorecard_{agency.short_code}_{pp['label']}.xlsx"
    build_workbook(
        clinician_df=clinician_df,
        agency_metrics=agency_metrics,
        pay_period=pp,
        clinic_key=clinic_key,
        output_path=xlsx_path,
    )

    # CSV export (for downstream tools / BI)
    csv_path = output_dir / f"QPi_Clinician_Data_{agency.short_code}_{pp['label']}.csv"
    clinician_df.to_csv(csv_path, index=False)
    logger.info(f"CSV exported: {csv_path}")

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"SCORECARD COMPLETE: {agency.name} — {pp['label']}")
    logger.info(f"{'='*60}")
    logger.info(f"Clinicians: {len(clinician_df)}")
    logger.info(f"Total Visits: {clinician_df['total_visits'].sum()}")
    logger.info(f"Total Points: {clinician_df['points'].sum():.0f}")
    logger.info(f"Meeting Target: {clinician_df['meets_target'].sum()}/{len(clinician_df)}")
    logger.info(f"Avg On-Time %: {clinician_df['on_time_pct'].mean():.1f}%")
    logger.info(f"Census: {agency_metrics.census}")
    cpv_vals = clinician_df["cpv"].dropna()
    if not cpv_vals.empty:
        logger.info(f"Avg CPV: ${cpv_vals.mean():.2f}")
    logger.info(f"Output: {xlsx_path}")
    logger.info(f"{'='*60}\n")

    return {
        "clinician_df": clinician_df,
        "agency_metrics": agency_metrics,
        "pay_period": pp,
        "xlsx_path": xlsx_path,
        "csv_path": csv_path,
    }


def main():
    parser = argparse.ArgumentParser(description="QPi Scorecard Automation")
    parser.add_argument("--pp", type=int, default=None,
                        help="Pay period number (0 or omit = most recent completed)")
    parser.add_argument("--agency", type=int, default=DEFAULT_CLINIC_KEY,
                        help=f"Clinic key (default: {DEFAULT_CLINIC_KEY})")
    parser.add_argument("--all-agencies", action="store_true",
                        help="Run for all agencies")
    parser.add_argument("--trend", action="store_true",
                        help="Include prior period comparison")
    parser.add_argument("--isolved-mode", choices=["csv", "api", "finch"],
                        default=None, help="Override iSolved mode")
    parser.add_argument("--log-file", type=str, default=None,
                        help="Also write logs to file (e.g. output/qpi.log)")

    args = parser.parse_args()

    # Pre-flight validation
    _validate_env()

    # Optional log file
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logging.getLogger().addHandler(fh)

    # Override iSolved mode if specified
    if args.isolved_mode:
        import config
        config.ISOLVED_MODE = args.isolved_mode

    if args.all_agencies:
        results = {}
        for ck, agency in AGENCIES.items():
            logger.info(f"\n{'#'*60}")
            logger.info(f"PROCESSING: {agency.name} (CK={ck})")
            logger.info(f"{'#'*60}")
            try:
                results[ck] = run_scorecard(ck, args.pp, args.trend)
            except Exception as e:
                logger.error(f"FAILED: {agency.name}: {e}", exc_info=True)
                results[ck] = None

        # Summary
        failed_count = sum(1 for r in results.values() if r is None)
        logger.info(f"\n{'='*60}")
        logger.info("ALL AGENCIES COMPLETE")
        logger.info(f"{'='*60}")
        for ck, res in results.items():
            agency = AGENCIES[ck]
            if res:
                logger.info(f"  {agency.name}: {res['xlsx_path']}")
            else:
                logger.info(f"  {agency.name}: FAILED")
        if failed_count > 0:
            sys.exit(1)
    else:
        run_scorecard(args.agency, args.pp, args.trend)


if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)
