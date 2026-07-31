"""
shifts.py - Admin roster and shift schedules.
All times are US Central Time (ET).
"""

from datetime import time

ADMINS = {
    1128711004: {"name": "Victor",  "username": "vmitrea"},
    503960467:  {"name": "Max",     "username": "ZavalniiMaxim"},
    1095527903: {"name": "Daniel",  "username": "Storm33S"},
    1373070156: {"name": "Anton",   "username": "AntonOgl"},
    8422260316: {"name": "Alex",    "username": "alexrepairs"},
    7769230456: {"name": "Andrei",  "username": "Andrei_Cr05"},
    7808593054: {"name": "Petru",   "username": "Petru S"},
    6054170642: {"name": "Ion",   "username": "Ion AH"},
    6855707802: {"name": "Mihai AH",   "username": "Mihai AH"},
    457540635: {"name": "Sergiu",   "username": "Lester_Fx"},
    8755804962: {"name": "Andrei",   "username": "maintenancetag"},
}

ALL_IDS = list(ADMINS.keys())

SHIFTS = [
    {
        "name": "Dayshift",
        "start": time(6, 30),   # 6:30 AM
        "end": time(16, 0),     # 4:00 PM
        "days": [0, 1, 2, 3, 4],
        "admins": ALL_IDS,
    },
    {
        "name": "AH",
        "start": time(16, 0),   # 4:00 PM
        "end": time(23, 0),     # 11:00 PM
        "days": [0, 1, 2, 3, 4],
        "admins": ALL_IDS,
    },
    {
        "name": "Morning",
        "start": time(23, 0),   # 11:00 PM
        "end": time(7, 0),      # 7:00 AM (next day)
        "days": [0, 1, 2, 3, 4, 5, 6],
        "admins": ALL_IDS,
    },
]

TIMEZONE = "America/Chicago"

# MAIN_ADMIN_ID is kept as a set so "user.id in MAIN_ADMIN_ID" works correctly everywhere
MAIN_ADMIN_ID = {8422260316, 7808593054, 7769230456, 1401145589}

SUPER_ADMINS = {8422260316, 7808593054, 7769230456, 1401145589}  # all super admins
