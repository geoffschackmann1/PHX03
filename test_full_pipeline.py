"""
Test 3: Full Pipeline
Snowflake + iSolved → Scorecard Engine → Excel Workbook
Usage: python test_full_pipeline.py
"""
import sys
sys.path.insert(0, '.')
from load_env import load_dotenv
load_dotenv()

from pathlib import Path
import pandas as pd

from config import (
    SNOWFLAKE_CONFIG, ISOLVED_MODE, ISOLVED_CSV_DIR,
    get_pay_period, get_prior_period,
)
from snowflake_client import SnowflakeClient
from isolved_client import get_payroll_adapter
from scorecard_engine import build_clinician_scorecard, build_agency_metrics
from excel_builder import build_workbook

print("=" * 60)
print("QPi SCORECARD — FULL PIPELINE TEST")
print("=" * 60)

clinic_key = 3743
pp = get_pay_period(4)
prior_pp = get_prior_period(pp)

print(f"\nCurrent Period: {pp['label']} ({pp['range_str']})")
print(f"Prior Period:   {prior_pp['label']} ({prior_pp['range_str']})")
print()

# ----------------------------------------------------------------
# STEP 1: Snowflake
# ----------------------------------------------------------------
print("STEP 1: Pulling Snowflake data...")
sf = SnowflakeClient(SNOWFLAKE_CONFIG)
sf.connect()
sf_data = sf.get_all(clinic_key, pp['start'], pp['end'])
prior_prod = sf.get_clinician_productivity(clinic_key, prior_pp['start'], prior_pp['end'])
prior_docs = sf.get_documentation(clinic_key, prior_pp['start'], prior_pp['end'])
sf.close()
print(f"  Current period: {len(sf_data['productivity'])} clinicians")
print(f"  Prior period:   {len(prior_prod)} clinicians")
print()

# ----------------------------------------------------------------
# STEP 2: iSolved
# ----------------------------------------------------------------
print("STEP 2: Loading iSolved data...")
payroll_adapter = get_payroll_adapter(mode=ISOLVED_MODE, csv_dir=ISOLVED_CSV_DIR)
try:
    payroll = payroll_adapter.get_payroll(pp['start'], pp['end'])
    print(f"  Loaded: {len(payroll)} employees")
except Exception as e:
    print(f"  Skipped: {e}")
    print("  (This is OK — scorecard will generate without payroll data)")
    payroll = pd.DataFrame()
print()

# ----------------------------------------------------------------
# STEP 3: Build Scorecard
# ----------------------------------------------------------------
print("STEP 3: Building scorecard...")
clinician_df = build_clinician_scorecard(
    sf_productivity=sf_data['productivity'],
    sf_documentation=sf_data['documentation'],
    payroll=payroll,
    prior_productivity=prior_prod,
    prior_documentation=prior_docs,
)
agency_metrics = build_agency_metrics(sf_data)

print(f"  Clinicians:      {len(clinician_df)}")
print(f"  Total Visits:    {clinician_df['total_visits'].sum()}")
print(f"  Total Points:    {clinician_df['points'].sum():.0f}")
print(f"  Meeting Target:  {clinician_df['meets_target'].sum()}/{len(clinician_df)}")
print(f"  Avg On-Time %:   {clinician_df['on_time_pct'].mean():.1f}%")
print(f"  Census:          {agency_metrics.census}")
print(f"  LUPA:            {agency_metrics.lupa_pct}%")
print(f"  SoC Medicare:    {agency_metrics.soc_medicare_pct}%")
print(f"  Asst Util:       {agency_metrics.assistant_pct}%")

has_payroll = 'regular_hours' in clinician_df.columns and clinician_df['regular_hours'].notna().any()
if has_payroll:
    cpv_vals = clinician_df['cpv'].dropna()
    if not cpv_vals.empty:
        print(f"  Avg CPV:         ${cpv_vals.mean():.2f}")
    pph_vals = clinician_df['points_per_hour'].dropna()
    if not pph_vals.empty:
        print(f"  Avg Pts/Hr:      {pph_vals.mean():.2f}")
else:
    print("  (Payroll data not loaded — CPV and Pts/Hr unavailable)")

print()

# ----------------------------------------------------------------
# STEP 4: Generate Excel
# ----------------------------------------------------------------
print("STEP 4: Generating Excel workbook...")
output_dir = Path('./output/test')
output_dir.mkdir(parents=True, exist_ok=True)
xlsx_path = output_dir / f"QPi_Scorecard_AP_{pp['label']}_TEST.xlsx"

build_workbook(
    clinician_df=clinician_df,
    agency_metrics=agency_metrics,
    pay_period=pp,
    clinic_key=clinic_key,
    output_path=xlsx_path,
)

# Also export raw CSV for debugging
csv_path = output_dir / f"QPi_Raw_Data_{pp['label']}_TEST.csv"
clinician_df.to_csv(csv_path, index=False)

print(f"  Excel: {xlsx_path}")
print(f"  CSV:   {csv_path}")

# ----------------------------------------------------------------
# VALIDATION
# ----------------------------------------------------------------
print()
print("=" * 60)
print("VALIDATION CHECKLIST")
print("=" * 60)

checks = [
    ("Clinician count ≥ 10", len(clinician_df) >= 10),
    ("Total visits = 181", clinician_df['total_visits'].sum() == 181),
    ("Berry points ≈ 60.5", abs(clinician_df[clinician_df['clinician_name'].str.contains('BERRY')]['points'].iloc[0] - 60.5) < 1),
    ("Census = 44", agency_metrics.census == 44),
    ("SoC Medicare > 90%", agency_metrics.soc_medicare_pct > 90),
    ("Excel file exists", xlsx_path.exists()),
    ("Excel file > 10KB", xlsx_path.exists() and xlsx_path.stat().st_size > 10000),
]

all_pass = True
for label, result in checks:
    status = "PASS" if result else "FAIL"
    if not result:
        all_pass = False
    print(f"  [{status}] {label}")

print()
if all_pass:
    print("ALL CHECKS PASSED — Pipeline is production-ready!")
    print(f"\nOpen the Excel file to verify: {xlsx_path}")
    print("\nTo run production: python main.py --pp 4 --trend")
else:
    print("SOME CHECKS FAILED — review output above")
    print("This may be expected if pay period data has changed since validation")
print("=" * 60)
