#!/usr/bin/env bash
# Render build step
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
# Admin account from environment variables (free plan has no shell). Safe to repeat.
if [ -n "$DJANGO_SUPERUSER_USERNAME" ]; then
  python manage.py createsuperuser --noinput || true
fi
# Optional example data for demos
if [ "$SEED_DEMO" = "1" ]; then
  python manage.py seed_demo
fi
