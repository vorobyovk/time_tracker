#!/usr/bin/env python3
# /var/www/python_app/app.wsgi

import sys
import os

# Добавляем директорию проекта в PYTHONPATH, чтобы Python мог найти ваш app.py
project_home = '/var/www/python_app'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from app import app as application