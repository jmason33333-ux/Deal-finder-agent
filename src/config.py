"""Configuration management - loads secrets and app settings."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Project root
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = DATA_DIR / "logs"

# Load secrets
_secrets_path = CONFIG_DIR / "secrets.env"
if _secrets_path.exists():
    load_dotenv(_secrets_path)

# API keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
FLIPPA_API_KEY = os.getenv("FLIPPA_API_KEY", "")

# Google Sheets (via Apps Script web app — no credentials file needed)
GOOGLE_APPS_SCRIPT_URL = os.getenv("GOOGLE_APPS_SCRIPT_URL", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Search filters
FILTERS = {
    "platform": "shopify",
    "min_price": 100_000,
    "max_price": 500_000,
    "business_model": "ecommerce",
}

# Scoring thresholds
SCORE_THRESHOLD_AI_ANALYSIS = 65
SCORE_THRESHOLD_SHEET = 80  # STRONG BUY only

# Claude model for analysis
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
