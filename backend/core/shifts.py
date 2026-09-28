"""Shift schedule only. User IDs, names and roles live in DATA_DIR/users.json."""
from datetime import time

SHIFTS = [
    {"name": "Dayshift", "start": time(6, 30), "end": time(16, 0), "days": [0,1,2,3,4]},
    {"name": "AH",       "start": time(16, 0), "end": time(23, 0), "days": [0,1,2,3,4]},
    {"name": "Morning",  "start": time(23, 0), "end": time(7, 0),  "days": [0,1,2,3,4,5,6]},
]
TIMEZONE = "America/Chicago"
