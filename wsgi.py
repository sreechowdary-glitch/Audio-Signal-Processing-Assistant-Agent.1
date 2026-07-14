"""
wsgi.py — Production WSGI entry point for Gunicorn / IBM Cloud Foundry.

Usage:
    gunicorn wsgi:application
"""

from app import create_app

application = create_app("production")
