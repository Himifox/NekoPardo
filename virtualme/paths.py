"""Stable project paths without loading environment-dependent settings."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
