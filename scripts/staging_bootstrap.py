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
    if uid <= 0:
        raise RuntimeError("STAGING_DEVELOPER_ID must be positive")
    if drivers >= 0:
        raise RuntimeError("DRIVER_GROUP_ID must be a negative Telegram group ID")
    if reports >= 0:
        raise RuntimeError("REPORTS_GROUP_ID must be a negative Telegram group ID")
    if drivers == reports:
        raise RuntimeError("Staging requires distinct driver and report groups")
    if not os.environ.get("BOT_TOKEN") or not os.environ.get("BOT_USERNAME"):
        raise RuntimeError("BOT_TOKEN or BOT_USERNAME is missing")
    existing = get_user(uid)
    if not (edit_role(uid, "developer") if existing else add_user(uid, "Staging Developer", "", "developer")):
        raise RuntimeError("Developer bootstrap failed")
    print("Staging developer bootstrap successful; starting Telegram polling", flush=True)
    if int(os.getenv("STAGING_SEED_CASES") or "0") > 0:
        from scripts.seed_staging import seed
        seed(int(os.environ["STAGING_SEED_CASES"]))
    if os.getenv("STAGING_BOARD_CAPACITY") == "1":
        from scripts.seed_board_capacity import seed_capacity
        seed_capacity()
    if os.getenv("STAGING_REALISTIC_FIXTURE") == "1":
        from scripts.realistic_board_fixture import enrich
        enrich()
    if int(os.getenv("STAGING_SIM_AGENTS") or "0") > 0:
        from scripts.staging_presence import start
        start(int(os.environ["STAGING_SIM_AGENTS"]), int(os.getenv("STAGING_SIM_SECONDS", "1800")))
    if int(os.getenv("STAGING_WORKLOAD_AGENTS") or "0") > 0:
        from scripts.staging_workload import start as start_workload
        start_workload(int(os.environ["STAGING_WORKLOAD_AGENTS"]), int(os.getenv("STAGING_WORKLOAD_SECONDS","600")))
    runpy.run_module("bot", run_name="__main__")

if __name__ == "__main__":
    main()
