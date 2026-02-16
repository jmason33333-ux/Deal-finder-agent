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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FLIPPA_API_KEY = os.getenv("FLIPPA_API_KEY", "")

# Google Sheets (via Apps Script web app — no credentials file needed)
GOOGLE_APPS_SCRIPT_URL = os.getenv("GOOGLE_APPS_SCRIPT_URL", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Qualification filters — hard pass/fail gates
FILTERS = {
    "min_price": 50_000,
    "max_price": 500_000,
    "min_monthly_profit": 5_000,
    "max_profit_multiple": 4.0,
    "min_net_margin_pct": 10.0,
    "min_business_age_months": 24,  # at least 2 years to prove the model
}

# Location filter — seller must be in the US
ALLOWED_LOCATIONS = {
    "us", "usa", "united states",
}

# Industries/niches to exclude — risky, regulated, or poor fit
EXCLUDED_INDUSTRIES = {
    "gambling", "casino", "betting", "poker",
    "smoking", "tobacco", "vape", "vaping", "cigarette",
    "medical equipment", "medical device", "pharmaceutical", "pharma",
    "firearms", "weapons", "ammunition", "guns",
    "adult", "xxx",
    "cannabis", "marijuana", "cbd",
    "cryptocurrency", "crypto", "forex", "binary options",
}

# Gemini model for AI summarization
GEMINI_MODEL = "gemini-2.5-flash-preview-05-20"
