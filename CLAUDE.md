# QPi Scorecard Automation — Project Rules

## What This Is
Automated Quality & Productivity Improvement (QPi) scorecard system for American Premier Home Health. Generates bi-weekly clinician and agency scorecards from Snowflake (EMR), iSolved (payroll), SHP (quality), and Hippocratic AI (NPS).

## Architecture
- **Snowflake**: Read-only access. Never attempt CREATE, INSERT, UPDATE, or ALTER.
  - Database: `WS_HHH_BI_DW_READONLY`, Schema: `WS`
  - Warehouse: `WH_HEALTH_MAX_GROUP_10026_XSM`, Role: `READER`
  - Auth: Key pair (preferred) or password via `.env`
  - Key path: `/Users/yourname/.snowflake/keys/rsa_key.p8` (use full path, not `~`)
- **iSolved**: Pluggable adapter pattern — CSV, direct API, or Finch. Never break the adapter interface.
- **All data processing happens in-memory via pandas** (no staging tables).
- **Output**: Excel workbooks (openpyxl) + CSV exports.

## Agency
- **American Premier Home Health** — Clinic Key: `3743`, Region: AZ, Pay-per-visit (PPV)
- PPV region — no actual hours from iSolved, estimate via best practice hours.

## Pay Periods
- Bi-weekly, Sunday–Saturday
- Anchor: PP4 starts 1/25/2026 (Sunday)
- Auto-calculated from anchor date in `config.py`

## Point System
- SOC/Eval (ADMISSION/ EVAL): **2.5 points**, 2.75 BP hours
- ROC: **2.0 points**, 2.75 BP hours
- Recertification (RECERTIFICATION/ REEVAL): **1.5 points**, 1.75 BP hours
- Routine Visit / Discharge: **1.0 point**, 1.25 BP hours
- Target: **30 points per pay period** (adjusted for time off)

## Critical Data Rules
- **Medicare filter**: Use `INSURANCE_TYPE ILIKE '%medicare%'` — NOT `INSURANCE_NAME` (which contains MAC names like "Noridian")
- **Documentation on-time**: `DATEDIFF('day', VISIT_DATE, FIRST_SUBMITTED_WITH_SIGNATURE_DATE) <= 2`
- **OASIS correction returns**: NOT counted as documentation errors
- **Visit category**: Snowflake lumps SOC and Eval under `ADMISSION/ EVAL` — cannot distinguish without iSolved earnings codes
- **Completed visits only**: Always filter `IS_COMPLETED = TRUE AND IS_MISSED_VISIT = FALSE`
- **Deleted records**: Always filter `IS_DELETED = FALSE` on both fact and dimension tables
- **Organization filter**: Always join DIM_ORGANIZATION and filter `CLINIC_TYPE = 'AGENCY MANAGER'`

## SQL Rules
- All SQL must use **Snowflake syntax** (not MySQL, Postgres, etc.)
- Use `%(param)s` placeholders for Python parameterized queries
- Always include `IS_DELETED = FALSE` on every table in every query
- Task type filter: `TASK_TYPE_ABBREVIATION IN ('SN','PT','OT','ST','MSW','LPN/LVN','PTA','COTA','HHA')`
- Visit category filter: `VISIT_CATEGORY IN ('ADMISSION/ EVAL','RECERTIFICATION/ REEVAL','ROUTINE VISIT','DISCHARGE')`

## Code Standards
- Python 3.9+
- Use type hints on function signatures
- Logging via `logging` module (not print statements in production code)
- Test files prefixed with `test_`
- Credentials in `.env` only — never hardcode

## File Structure
```
main.py              — CLI orchestrator (entry point)
config.py            — Settings, targets, pay period math
snowflake_client.py  — All Snowflake queries
isolved_client.py    — Payroll adapter (CSV/API/Finch)
scorecard_engine.py  — KPI calculations and data merge
excel_builder.py     — Excel workbook generator
load_env.py          — .env file loader
test_connection.py   — Test 1: Snowflake connectivity
test_queries.py      — Test 2: Query validation
test_full_pipeline.py — Test 3: End-to-end pipeline
```

## KPI Targets
| KPI | Target | Direction |
|-----|--------|-----------|
| Star Rating | ≥ 4 | SHP |
| NPS | > 90% | Hippocratic AI |
| Doc On-Time | > 90% | Snowflake |
| Productivity | ≥ 30 pts (adj) | Calculated |
| Utilization | ≥ 100% | Calculated |
| CPV | < $105 | Calculated |
| Census | > 125 | Snowflake |
| LUPA | ≤ 7% | Snowflake |
| Visits/Episode | ≤ 8.5 | Snowflake |
| SoC Medicare | > 80% | Snowflake |
| Assistant Util | > 40% | Snowflake |
| Overtime | < 20 hrs | iSolved |

## Assistant Disciplines
PTA, COTA, LPN/LVN, HHA — these are "assistant" level for utilization calculations.
SN, RN, PT, OT, ST, MSW — these are "skilled" level.
