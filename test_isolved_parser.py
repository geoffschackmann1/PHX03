"""
Test iSolved CSV parser.
Loads the test CSV and asserts row count, clinician name, and payroll columns.
Usage: python test_isolved_parser.py
"""
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolved_client import get_payroll_adapter

def main():
    adapter = get_payroll_adapter("csv", csv_dir="./input/isolved")
    df = adapter.get_payroll(date(2026, 1, 25), date(2026, 2, 7))

    assert len(df) == 6, f"Expected 6 employees, got {len(df)}"
    assert df["clinician_name"].iloc[0] == "DAVID BERRY", (
        f"Expected first clinician DAVID BERRY, got {df['clinician_name'].iloc[0]}"
    )
    assert df["gross_wages"].notna().any(), "Expected gross_wages to be populated"
    assert df["regular_hours"].notna().any(), "Expected regular_hours to be populated"
    assert "total_cost" in df.columns and df["total_cost"].notna().any(), (
        "Expected total_cost to be present and populated"
    )

    print("test_isolved_parser: All assertions passed.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
