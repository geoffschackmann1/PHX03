"""
Test 2: Scorecard Queries
Runs all Snowflake queries for PP4 and validates against known results.
Usage: python test_queries.py
"""
import sys
sys.path.insert(0, '.')
from load_env import load_dotenv
load_dotenv()

from config import SNOWFLAKE_CONFIG, CLINIC_KEY, AGENCY, get_pay_period
from snowflake_client import SnowflakeClient

print("=" * 50)
print("QPi SCORECARD — QUERY TEST")
print("=" * 50)

# Use PP4 — the period we validated manually
pp = get_pay_period(4)
print(f"\nPay Period: {pp['label']} ({pp['range_str']})")
print(f"Agency: {AGENCY.name} (CK={CLINIC_KEY})")
print()

sf = SnowflakeClient(SNOWFLAKE_CONFIG)
try:
    sf.connect()
    data = sf.get_all(clinic_key=CLINIC_KEY, pp_start=pp['start'], pp_end=pp['end'])

    # ---------------------------------------------------------------
    # Validate each query
    # ---------------------------------------------------------------
    passed = 0
    failed = 0

    # 1. Productivity
    prod = data['productivity']
    print(f"1. PRODUCTIVITY: {len(prod)} clinicians")
    if len(prod) >= 10:
        prod['points'] = (
            prod['soc_eval_ct'] * 2.5 + prod['recert_ct'] * 1.5
            + prod['routine_ct'] * 1.0 + prod['discharge_ct'] * 1.0
        )
        print(f"   Total visits: {prod['total_visits'].sum()}")
        print(f"   Total points: {prod['points'].sum():.0f}")
        print("   Top 5:")
        for _, row in prod.nlargest(5, 'points').iterrows():
            print(f"     {row['clinician_name']:25s} {row['discipline']:6s} "
                  f"V={int(row['total_visits']):3d}  P={row['points']:.1f}")
        # Expected: Berry ~60.5, Dehn ~32.5, Gonzales ~32.5
        berry = prod[prod['clinician_name'].str.contains('BERRY', case=False)]
        if not berry.empty and berry.iloc[0]['points'] > 55:
            print("   VALIDATED: Berry points match expected range")
            passed += 1
        else:
            print("   WARNING: Berry points don't match expected ~60.5")
            failed += 1
    else:
        print("   WARNING: Expected ~15 clinicians")
        failed += 1

    print()

    # 2. Documentation
    docs = data['documentation']
    print(f"2. DOCUMENTATION: {len(docs)} clinicians")
    if not docs.empty:
        avg_ot = docs['on_time_pct'].mean()
        print(f"   Avg on-time: {avg_ot:.1f}%")
        # Expected: ~82% (range allows for data variance)
        if 60 <= avg_ot <= 95:
            print("   VALIDATED: Avg on-time in expected range")
            passed += 1
        else:
            print(f"   WARNING: Expected ~82%, got {avg_ot:.1f}%")
            failed += 1
    else:
        print("   WARNING: No documentation data returned")
        failed += 1

    print()

    # 3. Census
    census = data['census']
    if not census.empty:
        val = int(census.iloc[0]['census_count'])
        print(f"3. CENSUS: {val}")
        # Expected: ~44
        if 30 < val < 60:
            print("   VALIDATED: Census in expected range")
            passed += 1
        else:
            print(f"   WARNING: Expected ~44, got {val}")
            failed += 1
    else:
        print("3. CENSUS: No data returned")
        failed += 1

    print()

    # 4. LUPA
    lupa = data['lupa']
    if not lupa.empty:
        val = float(lupa.iloc[0]['lupa_pct'] or 0)
        print(f"4. LUPA: {val}%")
        passed += 1
    else:
        print("4. LUPA: No data returned")
        failed += 1

    print()

    # 5. Non-Admits
    na = data['non_admits']
    if not na.empty:
        val = int(na.iloc[0]['non_admit_count'])
        print(f"5. NON-ADMITS: {val}")
        passed += 1
    else:
        print("5. NON-ADMITS: No data returned")
        failed += 1

    print()

    # 6. SoC Medicare
    med = data['soc_medicare']
    if not med.empty:
        val = float(med.iloc[0]['medicare_pct'] or 0)
        print(f"6. SOC MEDICARE: {val}%")
        # Expected: 95%
        if val > 80:
            print("   VALIDATED: Medicare % in expected range")
            passed += 1
        else:
            print(f"   WARNING: Expected ~95%, got {val}%")
            failed += 1
    else:
        print("6. SOC MEDICARE: No data returned")
        failed += 1

    print()

    # 7. Assistant Utilization
    asst = data['assistant_util']
    if not asst.empty:
        print(f"7. ASSISTANT UTIL:")
        for _, row in asst.iterrows():
            print(f"   {row['skill_level']}: {int(row['visit_count'])} visits")
        passed += 1
    else:
        print("7. ASSISTANT UTIL: No data returned")
        failed += 1

    # Summary
    print()
    print("=" * 50)
    print(f"RESULTS: {passed} passed, {failed} failed")
    if failed == 0:
        print("ALL QUERY TESTS PASSED")
        print("\nNext step: Run test_full_pipeline.py")
    else:
        print("SOME TESTS FAILED — review output above")
    print("=" * 50)

except Exception as e:
    print(f"FAILED: {e}")
    import traceback
    traceback.print_exc()
finally:
    sf.close()
