"""
config.py - All secrets from environment variables on Railway.
Locally falls back to encrypted files + config.ini.
"""

import os
import sys
import getpass
import configparser
from pathlib import Path

BASE_DIR = Path(__file__).parent
INI_FILE = BASE_DIR / "config.ini"


def _load_token():
    # Always check env first
    token = os.getenv("BOT_TOKEN", "").strip()
    if token:
        return token

    # Local encrypted file
    try:
        from cryptography.fernet import Fernet
        KEY_FILE   = BASE_DIR / ".secret.key"
        TOKEN_FILE = BASE_DIR / ".bot_token"
        if KEY_FILE.exists() and TOKEN_FILE.exists():
            return Fernet(KEY_FILE.read_bytes()).decrypt(TOKEN_FILE.read_bytes()).decode()
    except Exception:
        pass

    # Interactive — only works locally
    try:
        print("\n══════════════════════════════════════")
        print("  Kurtex Alert Bot - First Run Setup")
        print("══════════════════════════════════════")
        token = getpass.getpass("Paste your bot token: ").strip()
        if not token:
            sys.exit("No token provided.")
        try:
            from cryptography.fernet import Fernet
            KEY_FILE   = BASE_DIR / ".secret.key"
            TOKEN_FILE = BASE_DIR / ".bot_token"
            key = Fernet.generate_key()
            KEY_FILE.write_bytes(key)
            KEY_FILE.chmod(0o600)
            TOKEN_FILE.write_bytes(Fernet(key).encrypt(token.encode()))
            TOKEN_FILE.chmod(0o600)
            print("[OK] Token saved.\n")
        except ImportError:
            pass
        return token
    except (EOFError, OSError):
        # More helpful error message for Railway/cloud deployments
        print("\n" + "=" * 50)
        print("ERROR: BOT_TOKEN environment variable is required!")
        print("=" * 50)
        print("\nFor Railway deployment, set these environment variables:")
        print("  - BOT_TOKEN        (your Telegram bot token)")
        print("  - DRIVER_GROUP_ID (driver group chat ID)")
        print("  - REPORTS_GROUP_ID(reports group chat ID)")
        print("\nLearn more: https://docs.railway.app/develop/variables")
        sys.exit("Missing required environment variable: BOT_TOKEN")


def _load_ini():
    # Check env vars first
    driver  = os.getenv("DRIVER_GROUP_ID", "")
    reports = os.getenv("REPORTS_GROUP_ID", "")
    if driver and reports:
        return None  # use env vars directly

    ini = configparser.ConfigParser()
    if INI_FILE.exists():
        ini.read(INI_FILE)
        return ini

    # Interactive — only works locally
    try:
        print("\n══════════════════════════════════════")
        print("  Channel Setup")
        print("══════════════════════════════════════")
        driver  = input("Driver group ID: ").strip()
        reports = input("Reports group ID: ").strip()
        ini["channels"] = {"driver_group_id": driver, "reports_group_id": reports}
        with open(INI_FILE, "w") as fh:
            ini.write(fh)
        print("[OK] Config saved.\n")
        return ini
    except (EOFError, OSError):
        # More helpful error message for Railway/cloud deployments
        print("\n" + "=" * 50)
        print("ERROR: DRIVER_GROUP_ID and REPORTS_GROUP_ID required!")
        print("=" * 50)
        print("\nFor Railway deployment, set these environment variables:")
        print("  - BOT_TOKEN        (your Telegram bot token)")
        print("  - DRIVER_GROUP_ID (driver group chat ID)")
        print("  - REPORTS_GROUP_ID(reports group chat ID)")
        print("\nLearn more: https://docs.railway.app/develop/variables")
        sys.exit("Missing required environment variables: DRIVER_GROUP_ID and REPORTS_GROUP_ID")


class Config:
    TELEGRAM_TOKEN   = _load_token()
    _ini             = _load_ini()
    DRIVER_GROUP_ID  = int(os.getenv("DRIVER_GROUP_ID")  or (_ini.get("channels", "driver_group_id",  fallback="0") if _ini else "0"))
    REPORTS_GROUP_ID = int(os.getenv("REPORTS_GROUP_ID") or (_ini.get("channels", "reports_group_id", fallback="0") if _ini else "0"))
    AI_ALERTS_CHANNEL_ID = int(os.getenv("AI_ALERTS_CHANNEL_ID") or (_ini.get("channels", "ai_alerts_channel_id", fallback="0") if _ini else "0"))


config = Config()