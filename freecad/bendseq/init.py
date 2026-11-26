"""Headless namespace startup; no GUI dependencies."""
from . import enabled

if enabled():
    from . import headless
