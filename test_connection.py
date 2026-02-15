"""
Test 1: Snowflake Connection
Run this FIRST to verify your credentials work.
Usage: python test_connection.py
"""
import sys
sys.path.insert(0, '.')
from load_env import load_dotenv
load_dotenv()

from config import SNOWFLAKE_CONFIG

print("=" * 50)
print("QPi SCORECARD — SNOWFLAKE CONNECTION TEST")
print("=" * 50)
print()
print("Config:")
print(f"  Account:   {SNOWFLAKE_CONFIG['account']}")
print(f"  User:      {SNOWFLAKE_CONFIG['user']}")
print(f"  Role:      {SNOWFLAKE_CONFIG['role']}")
print(f"  Warehouse: {SNOWFLAKE_CONFIG['warehouse']}")
print(f"  Database:  {SNOWFLAKE_CONFIG['database']}")
print(f"  Key Path:  {SNOWFLAKE_CONFIG['private_key_path'] or '(not set)'}")
print(f"  Password:  {'***set***' if SNOWFLAKE_CONFIG['password'] else '(not set)'}")
print()

# Check auth is configured
if not SNOWFLAKE_CONFIG['private_key_path'] and not SNOWFLAKE_CONFIG['password']:
    print("ERROR: No authentication configured!")
    print("Edit .env and set either SF_PRIVATE_KEY_PATH or SF_PASSWORD")
    sys.exit(1)

from snowflake_client import SnowflakeClient

sf = SnowflakeClient(SNOWFLAKE_CONFIG)
try:
    sf.connect()
    print("SUCCESS: Connected to Snowflake!")
    print()

    # Verify context
    cur = sf.conn.cursor()
    cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE()")
    row = cur.fetchone()
    print(f"  User:      {row[0]}")
    print(f"  Role:      {row[1]}")
    print(f"  Warehouse: {row[2]}")
    print(f"  Database:  {row[3]}")
    cur.close()

    # Quick data check
    cur = sf.conn.cursor()
    cur.execute("SELECT COUNT(*) FROM FACT_PATIENT_TASK WHERE IS_DELETED = FALSE")
    count = cur.fetchone()[0]
    print(f"\n  FACT_PATIENT_TASK rows: {count:,}")
    cur.close()

    print("\n" + "=" * 50)
    print("CONNECTION TEST PASSED")
    print("=" * 50)
    print("\nNext step: Run test_queries.py")

except Exception as e:
    print(f"FAILED: {e}")
    print()
    print("Troubleshooting:")
    print("  1. SF_ACCOUNT: Just the hostname, no https://")
    print("     Example: wellsky_abc123.snowflakecomputing.com")
    print("  2. SF_USER: Your Snowflake service user (e.g., COMPANY_SNOWFLAKE_SVC)")
    print("  3. SF_PRIVATE_KEY_PATH: Full path to your .p8 file (not ~)")
    print("     Example: /Users/yourname/.snowflake/keys/rsa_key.p8")
    print("  4. If using password, set SF_PASSWORD in .env")
    print("  5. Check key file permissions: chmod 600 on .p8 file")
    print()
    import traceback
    traceback.print_exc()
finally:
    sf.close()
