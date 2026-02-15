# QPi Scorecard Automation — Deployment & Testing Guide (macOS)

## Claude vs Cursor vs Claude Code: When to Use Which

| Tool | Use For | Why |
|------|---------|-----|
| **This Claude project** | Designing queries, analyzing data, building new scorecard features, debugging logic | Claude has all your project knowledge (Snowflake schema, QPi SOP, KPI targets, payroll mapping). It's your data architect. |
| **Claude Code (in Cursor)** | Running commands, debugging errors, making code changes, git workflow | Reads your actual files, runs terminal commands, fixes code in place. Your hands-on developer. |
| **Cursor** | Browsing files, quick inline edits, visual git diffs | Your IDE — where you see and review everything. |

**The workflow**: Design here in Claude → hand off to Claude Code in Cursor → review results in Cursor.

---

## STEP 1: Set Up Your Project in Cursor

### 1A. Create the project folder

Open Cursor. Open a terminal (`` Ctrl+` `` or View → Terminal).

```bash
mkdir -p ~/Projects/qpi_automation
cd ~/Projects/qpi_automation
```

### 1B. Unzip the project files

Download `qpi_automation.zip` from this Claude conversation. Then:

```bash
# If saved to Downloads:
unzip ~/Downloads/qpi_automation.zip -d ~/Projects/qpi_automation

# The files end up nested — move them up:
mv ~/Projects/qpi_automation/qpi_automation/* ~/Projects/qpi_automation/
mv ~/Projects/qpi_automation/qpi_automation/.* ~/Projects/qpi_automation/ 2>/dev/null
rmdir ~/Projects/qpi_automation/qpi_automation
```

After extraction your folder should look like:

```
~/Projects/qpi_automation/
├── main.py
├── config.py
├── snowflake_client.py
├── isolved_client.py
├── scorecard_engine.py
├── excel_builder.py
├── requirements.txt
├── .env.template
├── .gitignore
├── CLAUDE.md
├── DEPLOYMENT_GUIDE.md
├── README.md
├── load_env.py
├── test_connection.py
├── test_queries.py
└── test_full_pipeline.py
```

**In Cursor**: File → Open Folder → select `~/Projects/qpi_automation`

You should see all the Python files in the Cursor sidebar.

---

## STEP 2: Install Python & Dependencies

### 2A. Check if Python is installed

In the Cursor terminal:

```bash
python3 --version
```

You need **Python 3.9 or higher**. If it's missing or too old:

```bash
# Install Homebrew first (if you don't have it):
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Then install Python:
brew install python@3.12
```

### 2B. Create a virtual environment

```bash
cd ~/Projects/qpi_automation

# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate
```

You should see `(venv)` at the beginning of your terminal prompt. **Every time you open a new terminal in Cursor, reactivate with:**
```bash
source venv/bin/activate
```

### 2C. Install dependencies

```bash
pip install -r requirements.txt
```

Takes 2-3 minutes. You should see "Successfully installed" at the end.

---

## STEP 3: Configure Snowflake Connection

Your Snowflake credentials:

| Setting | Value |
|---------|-------|
| Account | `wellsky-ws_health_max_group_10026` (locator only, not full URL) |
| Username | `HEALTH_MAX_GROUP_SNOWFLAKE_ADMIN` |
| Role | `READER` (already granted) |
| Warehouse | `WH_HEALTH_MAX_GROUP_10026_XSM` |
| Database | `WS_HHH_BI_DW_READONLY` |
| Schema | `WS` |
| Auth | Password |

### 3A. Create the .env file

```bash
cp .env.template .env
```

### 3B. Edit .env in Cursor

Open `.env` and update it to:

```bash
# SNOWFLAKE
SF_ACCOUNT=wellsky-ws_health_max_group_10026
SF_USER=HEALTH_MAX_GROUP_SNOWFLAKE_ADMIN
SF_ROLE=READER
SF_WAREHOUSE=WH_HEALTH_MAX_GROUP_10026_XSM

# Password auth
SF_PRIVATE_KEY_PATH=
SF_PASSWORD=YOUR_PASSWORD_HERE

# iSolved
ISOLVED_MODE=csv
ISOLVED_CSV_DIR=./input/isolved

# Output
QPi_OUTPUT_DIR=./output
```

Replace `YOUR_PASSWORD_HERE` with your actual Snowflake password. Everything else is already correct.

### Future: Key Pair Auth (optional, more secure for production)

Once this is running, you can switch to key pair authentication for production use. This eliminates the password from the `.env` file:

```bash
mkdir -p ~/.snowflake/keys
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out ~/.snowflake/keys/rsa_key.p8 -nocrypt
openssl rsa -in ~/.snowflake/keys/rsa_key.p8 -pubout -out ~/.snowflake/keys/rsa_key.pub
chmod 600 ~/.snowflake/keys/rsa_key.p8
```

Then register the public key in Snowsight and update `.env` to use `SF_PRIVATE_KEY_PATH` instead of `SF_PASSWORD`.

---

## STEP 4: Test Snowflake Connection (Test 1)

Make sure your venv is active (`source venv/bin/activate`), then:

```bash
python3 test_connection.py
```

**Expected output:**
```
Snowflake Config:
  Account:   wellsky####.snowflakecomputing.com
  User:      YOUR_SVC_USER
  ...
SUCCESS: Connected to Snowflake!
  User:      YOUR_SVC_USER
  Role:      READER
  Warehouse: WH_HEALTH_MAX_GROUP_10026_XSM

CONNECTION TEST PASSED
```

**If it fails** — see Troubleshooting at the bottom.

---

## STEP 5: Test Scorecard Queries (Test 2)

```bash
python3 test_queries.py
```

**Expected output** (should match Snowsight validation):
```
Pay Period: PP4 (01/25/2026 – 02/07/2026)

QUERY RESULTS:
  Productivity:  15 clinicians
  Documentation: 15 clinicians
  Census:        44
  LUPA %:        10.2%
  SoC Medicare:  95.0%

TOP 5 BY POINTS:
  DAVID BERRY               PT     Visits= 50  Points=60.5
  ...

ALL QUERY TESTS PASSED
```

---

## STEP 6: Test Full Pipeline (Test 3)

### 6A. Set up iSolved test data

```bash
mkdir -p input/isolved
```

Create `input/isolved/isolved_20260125_20260207.csv` with test data:

```csv
clinician_name,regular_hours,overtime_hours,gross_wages,mileage,total_cost,vacation_hours,pto_hours,sick_hours,holiday_hours,bereavement_hours
DAVID BERRY,80,0,4230.77,0,4230.77,0,0,0,0,0
ARNOLD GONZALES,80,0,4423.08,0,4423.08,0,0,0,0,0
DARRIUS GLYNN,82,0,3561.54,0,3561.54,16,0,0,0,0
BRENLEY TURNER,85,0,3799.85,0,3799.85,0,0,0,0,0
JOYCE PORNMANY,82,0,2932.62,0,2932.62,0,0,0,0,0
KIMBERLY DEHN,64,0,3823.36,0,3823.36,0,0,0,0,0
```

### 6B. Run the full pipeline

```bash
python3 test_full_pipeline.py
```

If this produces an Excel file in `output/test/`, you're done. Open it with Numbers or Excel for Mac and verify.

---

## STEP 7: Set Up Git & GitHub

```bash
cd ~/Projects/qpi_automation
git init
git add .
git commit -m "QPi scorecard automation v1 — initial commit"
```

Create a **private** repo on github.com (e.g. `PHX03`), then:

```bash
git remote add origin git@github.com:geoffschackmann1/PHX03.git
git branch -M main
git push -u origin main
```

---

## STEP 8: Run Production

```bash
source venv/bin/activate

# Most recent completed pay period
python3 main.py

# Specific period with trending
python3 main.py --pp 4 --trend

# All agencies
python3 main.py --pp 4 --all-agencies --trend

# With log file (for cron/scheduled runs)
python3 main.py --pp 4 --trend --log-file output/qpi.log

# Or use the run.sh wrapper (activates venv, passes args through)
./run.sh --pp 4 --trend
```

Output goes to `./output/PP4_AP/`

---

## STEP 9: Set Up Claude Code in Cursor

1. Open Cursor → Extensions (Cmd+Shift+X) → search "Claude Code" → Install
2. Open the Claude Code panel
3. Sign in with your Anthropic account
4. Tell it: **"Read the CLAUDE.md and DEPLOYMENT_GUIDE.md, then help me verify everything is set up correctly."**

---

## Troubleshooting (macOS)

| Error | Fix |
|-------|-----|
| `command not found: python3` | `brew install python@3.12` |
| `command not found: pip` | Use `pip3` or `python3 -m pip install ...` |
| `ModuleNotFoundError` | venv not active — run `source venv/bin/activate` |
| `zsh: permission denied` | Use `python3 filename.py` instead of `./filename.py` |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Run `/Applications/Python\ 3.12/Install\ Certificates.command` |
| `No .env file found` | `cp .env.template .env` and fill in values |
| `Private key could not be deserialized` | Use full path not `~` in .env. Check `chmod 600` on key. |
| `Could not find host` | SF_ACCOUNT: use account locator only, e.g. `wellsky-ws_health_max_group_10026` |
| `404 Not Found` / `...snowflakecomputing.com.snowflakecomputing.com` | SF_ACCOUNT must be locator only (no `.snowflakecomputing.com`). Use `wellsky-ws_health_max_group_10026` |
| `Warehouse does not exist` | Verify warehouse name in `config.py` |
| `No iSolved CSV found` | Put CSV in `input/isolved/` with date in filename |
| `xcrun: error` (after macOS update) | Run `xcode-select --install` |
| LibreSSL key errors | `brew install openssl` and regenerate key with Homebrew's OpenSSL |

---

## What's Next After Deployment

1. **Tune the iSolved CSV parser** — upload your actual export to the Claude project
2. **Add PDF clinician one-pagers** — individual scorecards per clinician
3. **Expand to all 3 agencies** — Casa Grande (11174) and American Excel (11944)
4. **Schedule it** — `crontab -e` or macOS `launchd` to run every 2 weeks
5. **Add SHP + NPS data** — manual CSV staging, then automate
