"""Stable ASGI entrypoint; composition lives in app.api.http."""

from .api.http import app

__all__ = ["app"]
