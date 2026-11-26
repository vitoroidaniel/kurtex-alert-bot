"""Read-only SQLite integrity and duplicate-request audit. No repairs are made."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

def audit(path):
    path=Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)) as conn:
        conn.execute("BEGIN")
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result={"integrity_ok":conn.execute("PRAGMA quick_check").fetchall()==[("ok",)],
                "foreign_key_violations":sum(1 for _ in conn.execute("PRAGMA foreign_key_check"))}
        if "cases" in tables:
            result["cases"]=conn.execute("SELECT count(*) FROM cases").fetchone()[0]
            result["duplicate_request_groups"]=conn.execute("""SELECT count(*) FROM (
                SELECT workspace_request_id,created_by_id FROM cases
                WHERE workspace_request_id IS NOT NULL AND workspace_request_id <> ''
                GROUP BY workspace_request_id,created_by_id HAVING count(*)>1)""").fetchone()[0]
            for table in ("case_notes","case_attachments"):
                if table in tables:
                    result["orphan_"+table]=conn.execute(
                        f"SELECT count(*) FROM {table} c LEFT JOIN cases p ON p.id=c.case_id WHERE p.id IS NULL"
                    ).fetchone()[0]
        return result

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    args=parser.parse_args()
    result=audit(args.database)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result["integrity_ok"] and not any(v for k,v in result.items() if k.startswith("orphan_") or k in ("foreign_key_violations","duplicate_request_groups")) else 1)
