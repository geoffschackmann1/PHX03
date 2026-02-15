# QPi Scorecard Automation
## American Premier Home Health

Automated Quality & Productivity Improvement (QPi) scorecard system. Pulls data from Snowflake (EMR) and iSolved (Payroll), calculates all clinician and agency KPIs, and generates Excel workbooks per pay period.

---

## Quick Start

```bash
# 1. Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.template .env
# Edit .env with your Snowflake credentials

# 3. Test Snowflake connection
python3 test_connection.py

# 4. Test scorecard queries
python3 test_queries.py

# 5. Place iSolved CSV in input folder
mkdir -p input/isolved
cp /path/to/payroll_register.csv input/isolved/

# 6. Run full pipeline test
python3 test_full_pipeline.py

# 7. Run for most recent completed pay period
python3 main.py

# 8. Run for specific period with trending
python3 main.py --pp 4 --trend

# 9. Run all agencies
python3 main.py --pp 4 --all-agencies --trend
```

---

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Snowflake   │     │   iSolved    │     │  SHP / NPS   │
│  (Kinnser)   │     │  (Payroll)   │     │  (Phase 2+)  │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       │ snowflake-connector│ CSV / API / Finch  │ Manual CSV
       │                    │                    │
       ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────┐
│                  SCORECARD ENGINE                        │
│  • Merge by clinician name                              │
│  • Calculate points, BP hours, adjusted targets         │
│  • CPV, pts/hr, utilization, visit mix                  │
│  • Prior period trending                                │
└────────────────────────┬────────────────────────────────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
        ┌──────────┐ ┌────────┐ ┌────────┐
        │  Excel   │ │  CSV   │ │  PDF   │
        │ Workbook │ │ Export │ │ (TBD)  │
        └──────────┘ └────────┘ └────────┘
```

---

## File Structure

```
qpi_automation/
├── main.py              # CLI orchestrator
├── config.py            # Settings, targets, pay period math
├── snowflake_client.py  # Snowflake queries (read-only)
├── isolved_client.py    # Payroll adapter (CSV/API/Finch)
├── scorecard_engine.py  # KPI calculations and data merge
├── excel_builder.py     # Excel workbook generator
├── requirements.txt     # Python dependencies
├── .env.template        # Environment variable template
├── input/
│   └── isolved/         # Drop iSolved CSVs here
└── output/
    └── PP4_AP/          # Generated per period + agency
        ├── QPi_Scorecard_AP_PP4.xlsx
        └── QPi_Clinician_Data_AP_PP4.csv
```

---

## Data Sources

| Source | Connection | Data |
|--------|-----------|------|
| Snowflake (Kinnser) | snowflake-connector-python, read-only | Visits, docs, census, LUPA, episodes, insurance |
| iSolved | CSV now, API/Finch later | Hours, OT, wages, mileage, time off, pay type |
| SHP | Manual portal export (Phase 2) | Star rating, OASIS outcomes, hospitalization |
| Hippocratic AI | Manual export (Phase 2) | NPS scores |

---

## Pay Period System

Pay periods are bi-weekly, Sunday–Saturday, anchored to PP4 = 1/25/2026.

```python
python3 main.py --pp 0   # Auto-detect most recent completed
python3 main.py --pp 4   # PP4: 1/25 – 2/7/2026
python3 main.py --pp 5   # PP5: 2/8 – 2/21/2026
python3 main.py --pp 3   # PP3: 1/11 – 1/24/2026
```

---

## iSolved Integration

### Current: CSV Mode
Export the Payroll Register from iSolved, save as CSV in `input/isolved/`. Name it with the pay period dates for auto-matching (e.g., `isolved_20260125_20260207.csv`).

### Future: API Mode
Set `ISOLVED_MODE=api` in `.env` and provide API credentials. The adapter will pull payroll data programmatically.

### Future: Finch Mode
Set `ISOLVED_MODE=finch` in `.env` with your Finch access token. Finch normalizes across 200+ payroll providers.

---

## Agencies

| Agency | Clinic Key | Region | Pay Model |
|--------|-----------|--------|-----------|
| American Premier | 3743 | AZ | PPV |
| Casa Grande | 11174 | AZ | PPV |
| American Excel | 11944 | NV | PPV |

---

## KPI Targets

| KPI | Target | Source |
|-----|--------|--------|
| Star Rating | ≥ 4 Stars | SHP |
| NPS | > 90% | Hippocratic AI |
| Doc On-Time | > 90% | Snowflake |
| Productivity | ≥ 30 pts (adjusted for time off) | Calculated |
| Utilization | ≥ 100% | Calculated |
| CPV | < $105 | Calculated |
| Census | > 125 | Snowflake |
| LUPA | ≤ 7% | Snowflake |
| Visits/Episode | ≤ 8.5 | Snowflake |
| SoC Medicare | > 80% | Snowflake |
| Assistant Util | > 40% | Snowflake |

---

## Roadmap

- [x] Phase 1: Python automation script
- [ ] Phase 2: iSolved API + SHP/NPS staging
- [ ] Phase 3: Multi-agency expansion (all 3 HH agencies)
- [ ] Phase 4: Scheduled execution + email distribution
- [ ] Phase 5: Power BI / Tableau dashboard
