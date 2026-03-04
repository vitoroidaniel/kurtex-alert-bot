"""
shifts.py - Admin roster and shift schedules.
All times are US Eastern Time (ET).
"""

from datetime import time

ADMINS = {
    899045897:  {"name": "Alexei",  "username": "ahusenberg"},
    1615926408: {"name": "Danika",  "username": "danikav"},
    #1401145589: {"name": "Ionut",   "username": "ionut_ci0banu"},
    #1128711004: {"name": "Victor",  "username": "vmitrea"},
    #503960467:  {"name": "Max",     "username": "ZavalniiMaxim"},
    #1095527903: {"name": "Daniel",  "username": "Storm33S"},
    #790341145:  {"name": "Sasa AH", "username": "Sasha5639"},
    #1373070156: {"name": "Anton",   "username": "AntonOgl"},
    8422260316: {"name": "Alex",    "username": "alexrepairs"},
    #7769230456: {"name": "Andrei",  "username": "Andrei_Cr05"},
}

ALL_IDS = list(ADMINS.keys())

SHIFTS = [
    {
        "name":   "Morning",
        "start":  time(6, 0),
        "end":    time(14, 0),
        "days":   [0, 1, 2, 3, 4],
        "admins": ALL_IDS,
    },
    {
        "name":   "Afternoon",
        "start":  time(14, 0),
        "end":    time(22, 0),
        "days":   [0, 1, 2, 3, 4],
        "admins": ALL_IDS,
    },
    {
        "name":   "Night",
        "start":  time(22, 0),
        "end":    time(6, 0),
        "days":   [0, 1, 2, 3, 4, 5, 6],
        "admins": ALL_IDS,
    },
    {
        "name":   "Weekend",
        "start":  time(8, 0),
        "end":    time(20, 0),
        "days":   [5, 6],
        "admins": ALL_IDS,
    },
]

TIMEZONE = "America/New_York"

MAIN_ADMIN_ID = 1615926408  # keep this as primary (used for reports fallback)

SUPER_ADMINS = {1615926408}  # all super admins
