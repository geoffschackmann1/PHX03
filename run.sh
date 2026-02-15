#!/bin/bash
# QPi Scorecard — Run wrapper (activates venv, passes args to main.py)
set -e
cd "$(dirname "$0")"
source venv/bin/activate
exec python3 main.py "$@"
