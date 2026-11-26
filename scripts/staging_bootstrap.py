"""Boot the isolated staging bot; refuse any other environment."""
import os
import runpy
from backend.storage.user_store import get_user, add_user, edit_role

def main():
    if (os.environ.get("RAILWAY_PROJECT_ID"), os.environ.get("RAILWAY_ENVIRONMENT_ID"), os.environ.get("DATA_DIR")) != ("b34e4565-81c2-4626-acd4-8437ef1dc77f", "2af7bf16-bac3-463f-a080-1fbd2148ee22", "/app/data"):
        raise RuntimeError("Refusing staging startup outside isolated project")
    uid = int(os.environ["STAGING_DEVELOPER_ID"])
    drivers = int(os.environ["DRIVER_GROUP_ID"])
    reports = int(os.environ["REPORTS_GROUP_ID"])
    if uid <= 0 or drivers >= 0 or reports >= 0 or drivers == reports or not os.environ.get("BOT_TOKEN") or not os.environ.get("BOT_USERNAME"):
        raise RuntimeError("Invalid staging bot configuration")
    existing = get_user(uid)
    if not (edit_role(uid, "developer") if existing else add_user(uid, "Staging Developer", "", "developer")):
        raise RuntimeError("Developer bootstrap failed")
    print("Staging developer bootstrap successful; starting Telegram polling", flush=True)
    runpy.run_module("bot", run_name="__main__")

if __name__ == "__main__":
    main()
