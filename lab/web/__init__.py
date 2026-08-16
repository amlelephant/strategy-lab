"""The Flask front end. `from lab.web.app import app` or `python run.py serve`."""

from .app import app, main

__all__ = ["app", "main"]
