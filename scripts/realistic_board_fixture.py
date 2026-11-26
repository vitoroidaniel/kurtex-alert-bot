"""Realistic fictional maintenance data for isolated staging only."""
import json, random, hashlib
from pathlib import Path
from datetime import datetime, timedelta
from backend.storage import db
from backend.storage.case_store import _upsert_case_row
from scripts.seed_staging import guard
from scripts.backup_sqlite import snapshot

SCENARIOS = [
("truck","Intermittent no-crank after overnight stop","Battery load test failed; replaced two batteries and cleaned terminals",380,780),
("trailer","Right rear tire losing pressure","Found puncture in tread; replaced tire and checked adjacent tire",230,620),
("truck","Coolant leak near radiator hose","Pressure test located leaking hose clamp; replaced hose and topped up coolant",180,540),
("trailer","ABS warning lamp stays on","Inspected wiring and replaced damaged wheel speed sensor",160,480),
("truck","DPF warning and reduced engine power","Shop diagnosed soot loading and completed stationary regeneration",250,850),
("trailer","Reefer temperature not holding setpoint","Checked alarm history; technician repaired electrical connector and verified temperature",320,1150),
("truck","Air leak near suspension valve","Located leaking fitting; replaced fitting and verified pressure retention",140,480),
("trailer","Landing gear difficult to operate","Inspected gearbox and lubricated mechanism; replaced worn handle",110,410),
("truck","Alternator output below specification","Tested charging circuit and replaced alternator",550,1250),
("trailer","Marker lights intermittent","Repaired corroded connector and verified lighting circuit",95,320),
("truck","Starter clicks but engine does not turn","Technician verified starter fault and replaced starter assembly",480,1050),
("trailer","Wheel end leaking lubricant","Shop replaced hub seal and inspected bearing condition",430,1200),
("truck","Steering vibration at highway speed","Shop inspected steering and tires; corrected wheel imbalance",180,560),
("trailer","Door seal damaged","Replaced damaged seal and checked door alignment",140,420),
("truck","Check engine lamp with DEF warning","Technician inspected DEF system and replaced failed sensor",370,1300),
("trailer","Brake chamber air leak","Qualified technician replaced defective chamber and verified brake operation",280,680)]
CITIES=["Dallas, TX","Memphis, TN","Atlanta, GA","Joliet, IL","Omaha, NE","Kansas City, MO","Indianapolis, IN","Columbus, OH","Phoenix, AZ","Denver, CO","Charlotte, NC","Nashville, TN"]
FIRST=["James","Michael","Robert","David","Carlos","Daniel","Maria","Anthony","Luis","Andrew","Victor","Alex"]
LAST=["Miller","Garcia","Wilson","Brown","Clark","Lewis","Walker","Hall","Young","Allen","King","Scott"]
def enrich():
    guard()
    root=Path("/app/data")
    backup=root/"staging-pre-realism-v1.sqlite3"
    if not backup.exists(): snapshot(root/"kurtex.sqlite3",backup)
    with db.read_conn(root) as conn:
        ids=[r[0] for r in conn.execute("SELECT id FROM cases WHERE id LIKE 'stg-capacity-v2-%' ORDER BY id")]
    updated=0
    distribution={}
    for start in range(0,len(ids),80):
        with db.write_tx(root) as conn:
            for cid in ids[start:start+80]:
                row=conn.execute("SELECT data_json FROM cases WHERE id=?",(cid,)).fetchone()
                if not row: continue
                c=json.loads(row[0])
                if c.get("synthetic_dataset")!="staging-capacity-v2" or c.get("realistic_fixture_v1"): continue
                rng=random.Random(int(hashlib.sha256(cid.encode()).hexdigest()[:16],16))
                typ,issue,fix,low,high=rng.choice(SCENARIOS)
                city=rng.choice(CITIES)
                driver=rng.choice(FIRST)+" "+rng.choice(LAST)+" (SIM)"
                cost=rng.randint(low,high)+rng.choice([0,.25,.50,.75,.99])
                done=c.get("status")=="done"
                count=rng.choices([1,2,3,4,5,6,8,10,12,15,20],[10,12,14,12,10,8,8,8,7,6,5])[0]
                opened=datetime.fromisoformat(c["opened_at"])
                messages=[f"Driver reports {issue.lower()} near {city}. Requested location and photos.",
                    f"Contacted fictional service center in {city}; checking technician availability.",
                    "Dispatcher notified; awaiting shop inspection and written estimate.",
                    f"Shop estimate: USD {cost:,.2f}. Requested authorization.",
                    "Driver confirmed unit is parked safely at service location.",
                    "Followed up with service advisor; waiting for parts and updated ETA.",
                    "Updated dispatch with revised service window.",
                    "Requested itemized invoice and technician findings.",
                    "Shop provided revised estimate; checked charges against approved work.",
                    "Confirmed next status update with driver."]
                notes=[]
                for k in range(count):
                    msg=messages[k%len(messages)]
                    if done and k==count-1: msg=fix+f". Final recorded cost: USD {cost:,.2f}."
                    notes.append(dict(id=cid+"-real-n"+str(k),author_id=c.get("agent_id"),author=c.get("agent_name"),text="SIM: "+msg,created_at=(opened+timedelta(minutes=k*15)).isoformat()))
                c.update(driver_name=driver,report_driver=driver,vehicle_type=typ,description=issue,issue_text=issue,shop_info="SIM "+city+" Fleet Service",amount=f"{cost:.2f}" if done or rng.random()>.25 else "",workspace_notes=notes,realistic_fixture_v1=True)
                if done:
                    c["resolution"]=fix
                    for h in c.get("workspace_history") or []:
                        if h.get("action")=="close":
                            h.update(author=c.get("agent_name"),author_id=c.get("agent_id"),created_at=c.get("closed_at"),fields=[])
                _upsert_case_row(conn,c,event_type="updated",actor_name="Staging Realistic Fixture")
                updated+=1
                distribution[count]=distribution.get(count,0)+1
    print("STAGING_REALISM_RESULT "+json.dumps(dict(updated=updated,note_distribution=distribution)),flush=True)
