#!/bin/bash
# One-command setup + demo for macOS / Linux.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo ">> Creating virtual environment"
  python3 -m venv .venv
  . .venv/bin/activate
  pip install --upgrade pip -q
  pip install -r requirements.txt -q
else
  . .venv/bin/activate
fi
[ -f sample_emails/inbox/01_balance_enquiry.eml ] || python -m scripts.generate_sample_emails
echo ">> Processing sample emails"
python main.py "$@"
