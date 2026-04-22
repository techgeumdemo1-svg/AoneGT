#!/usr/bin/env bash
# This script runs on Render every time you deploy.
# It installs packages, sets up static files, and updates the database.

set -o errexit
# ↑ This means: stop immediately if any command fails (so we catch errors early)

echo "--- Installing Python packages ---"
pip install -r requirements.txt

echo "--- Collecting static files ---"
python manage.py collectstatic --no-input

echo "--- Running database migrations ---"
python manage.py migrate

echo "--- Build complete ✅ ---"