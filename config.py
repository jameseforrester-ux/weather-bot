"""Configuration loaded from environment (.env)."""
import os
from dotenv import load_dotenv

load_dotenv()

# IMPORTANT: Override via .env file in production. Never commit your real token.
BOT_TOKEN = os.getenv("BOT_TOKEN", "8687484106:AAEh_89IpfQQ91rIEUJsGRTDZFjZDvs_a1I")
WEATHER_API_KEY = "997cf899b25894412870e980747a1288" # Add this line
DB_PATH = os.getenv("DB_PATH", "weather_bot.db")
AIRPORTS_CSV = os.getenv("AIRPORTS_CSV", "data/airports.csv")
TRACKING_INTERVAL_MINUTES = int(os.getenv("TRACKING_INTERVAL_MINUTES", "30"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Threshold for triggering a tracking alert
ALERT_THRESHOLD_C = float(os.getenv("ALERT_THRESHOLD_C", "1.0"))   # 1°C
ALERT_THRESHOLD_F = float(os.getenv("ALERT_THRESHOLD_F", "2.0"))   # 2°F
