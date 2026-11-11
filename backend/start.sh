#!/bin/sh
set -eu
# The project keeps schema changes in the models; generate/update migration files before applying them.
python manage.py makemigrations ppm_app --noinput
python manage.py migrate --noinput
python manage.py seed_demo
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 60
