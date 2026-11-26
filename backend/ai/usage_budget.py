"""Persistent per-user and workspace AI request budgets."""
import sqlite3
import time

def admit(path, user, category, user_limit, global_limit, window):
    now = int(time.time())
    with sqlite3.connect(str(path), timeout=5) as db:
        db.execute("CREATE TABLE IF NOT EXISTS ai_budget (actor TEXT, category TEXT, ts INTEGER)")
        db.execute("CREATE INDEX IF NOT EXISTS ai_budget_ts ON ai_budget(ts)")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM ai_budget WHERE ts <= ?", (now-86400,))
        cutoff = now-window
        count = db.execute("SELECT COUNT(*) FROM ai_budget WHERE actor=? AND category=? AND ts>?", (user,category,cutoff)).fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM ai_budget WHERE category=? AND ts>?", (category,cutoff)).fetchone()[0]
        if count >= user_limit or total >= global_limit:
            return False
        db.execute("INSERT INTO ai_budget VALUES (?,?,?)", (user,category,now))
        return True
