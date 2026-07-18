# Kurtex Alert Bot

Truck Maintenance Command Center — Telegram bot for managing driver alerts and cases.

## Project structure

## Data storage

All data is stored as JSON files in a Railway Volume mounted at `/data/`:

| File | Contents |
|---|---|
| `/app/data/cases.json` | All cases — permanent history |
| `/app/data/active_alerts.json` | Unassigned alerts — survives restarts |
| `/app/data/started_users.json` | Registered admin user IDs |

## Railway deployment

### 1. Add a Volume

In your Railway project → **New** → **Volume**
- Mount path: `/data`
- Attach to your bot service

### 2. Required environment variables

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `DRIVER_GROUP_ID` | Telegram group ID where drivers post alerts |
| `REPORTS_GROUP_ID` | Telegram group ID where case reports are sent |
| `AI_ALERTS_CHANNEL_ID` | Optional — channel ID for AI-detected alerts |
| `DATA_DIR` | Optional — defaults to `/data` (matches volume mount) |

### 3. Deploy

Push to GitHub, connect to Railway, add env vars, deploy.
Railway uses `python bot.py` as the start command per `railway.json`.

## Trigger words

Post any of these in the driver group to create an alert:
- `#maintenance`
- `#repairs`
- `#repair`

## Admin commands (private chat only)

| Command | Who |
|---|---|
| `/start` | Register with the bot |
| `/mycases` | Your active cases |
| `/done` | Today's closed cases |
| `/casehistory` | Full case history |
| `/mystats` | Your personal stats |
| `/shifts` | Current shift roster |
| `/oncall` | Who is reachable right now |
| `/help` | All commands |
| `/report` | Daily summary (super admin) |
| `/leaderboard` | Weekly top performers (super admin) |
| `/missed` | Unhandled alerts (super admin) |
