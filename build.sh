#!/usr/bin/env bash
# This script runs on Render every time you deploy.
# It installs packages, sets up static files, and updates the database.

set -o errexit
# ↑ This means: stop immediately if any command fails (so we catch errors early)

echo "--- Installing Python packages ---"
# Render → PyPI can hit 15s read timeouts; retry with a longer default.
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"
pip install --upgrade pip
pip install --default-timeout="${PIP_DEFAULT_TIMEOUT}" --retries 10 -r requirements.txt

echo "--- Collecting static files ---"
python manage.py collectstatic --no-input

echo "--- Running database migrations ---"
python manage.py migrate

echo "--- Build complete ✅ ---"