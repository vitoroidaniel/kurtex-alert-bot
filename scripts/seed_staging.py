"""Deterministic, additive staging dataset; never targets production."""
import os, time, shutil, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from backend.storage import db
from backend.storage.case_store import _upsert_case_row
from scripts.backup_sqlite import snapshot

PREFIX = "stg-load-v1-"
def guard():
    if (os.getenv("RAILWAY_PROJECT_ID"), os.getenv("RAILWAY_ENVIRONMENT_ID"), os.getenv("DATA_DIR")) != ("b34e4565-81c2-4626-acd4-8437ef1dc77f","2af7bf16-bac3-463f-a080-1fbd2148ee22","/app/data"):
        raise RuntimeError("Refusing synthetic data outside staging")
    if os.getenv("RAILWAY_VOLUME_MOUNT_PATH") != "/app/data":
        raise RuntimeError("Staging volume required")

def seed(total):
    guard()
    if not 1 <= total <= 50500:
        raise ValueError("Invalid seed size")
    start = time.monotonic()
    root = Path("/app/data")
    if shutil.disk_usage(root).free < 300*1024*1024:
        raise RuntimeError("Insufficient free space for seed")
    with db.read_conn(root) as conn:
        before = conn.execute("SELECT count(*) FROM cases WHERE id LIKE ?",(PREFIX+"%",)).fetchone()[0]
    backup = root / "staging-preload-v1.sqlite3"
    if not backup.exists():
        snapshot(root/"kurtex.sqlite3", backup)
    issues = ["Battery fails to hold charge","Air suspension leaking","Reefer temperature above setpoint","ABS warning illuminated","Coolant leaking near radiator","DPF regeneration required","Trailer tire sidewall damage","Starter intermittent failure"]
    now = datetime.now(timezone.utc)
    inserted = 0
    for base in range(0,total,250):
        with db.write_tx(root) as conn:
            for i in range(base,min(base+250,total)):
                cid = PREFIX+str(i).zfill(6)
                if conn.execute("SELECT 1 FROM cases WHERE id=?",(cid,)).fetchone():
                    continue
                active = i % 101 == 0
                opened = now-timedelta(days=(i%3 if active else 4+i%365),minutes=i%1440)
                stamp = opened.isoformat()
                end = (opened+timedelta(hours=2+i%18)).isoformat()
                agent = int(os.environ["STAGING_DEVELOPER_ID"])
                driver = "Synthetic Driver "+str(i%3000+1)
                issue = issues[i%len(issues)]
                case = dict(id=cid,status="reported" if active else "done",agent_id=agent,agent_name="Staging Developer",driver_name=driver,report_driver=driver,group_name="Synthetic Fleet",description=issue,issue_text=issue,opened_at=stamp,assigned_at=stamp,reported_at=stamp,updated_at=stamp if active else end,closed_at=None if active else end,source="workspace",workspace_request_id=cid,created_by_id=agent,vehicle_type="truck" if i%2 else "trailer",unit_number="S"+str(i%5000+1000),priority="normal",board_stage="",board_rank=float(i),shop_info="Synthetic Repair Center",amount=str(150+i%1800),load_type="Loaded" if i%2 else "Empty",workspace_notes=[dict(id=cid+"-n"+str(k),author_id=agent,author="Synthetic Agent",text="Synthetic maintenance update "+str(k+1),created_at=stamp) for k in range(5)],workspace_attachments=[],synthetic_dataset="staging-load-v1")
                _upsert_case_row(conn,case,event_type="created",actor_id=str(agent),actor_name="Synthetic Generator")
                inserted+=1
    with db.read_conn(root) as conn:
        count=conn.execute("SELECT count(*) FROM cases WHERE id LIKE ?",(PREFIX+"%",)).fetchone()[0]
        notes=conn.execute("SELECT count(*) FROM case_notes WHERE case_id LIKE ?",(PREFIX+"%",)).fetchone()[0]
        integrity=conn.execute("PRAGMA quick_check").fetchone()[0]
        fk=conn.execute("PRAGMA foreign_key_check").fetchone()
    if integrity!="ok" or fk:
        raise RuntimeError("Synthetic data integrity check failed")
    print("STAGING_SEED "+json.dumps(dict(before=before,inserted=inserted,cases=count,notes=notes,integrity=integrity,seconds=round(time.monotonic()-start,2))),flush=True)

if __name__=="__main__":
    seed(int(os.environ["STAGING_SEED_CASES"]))
