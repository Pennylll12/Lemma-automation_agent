#!/usr/bin/env bash
set -euo pipefail
python -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 required"'
python -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip check
AI_PROVIDER=mock LIVE_SEND_ENABLED=false EMBEDDED_WORKER=false .venv/bin/python -m pytest -q
node --check dist/app.js
