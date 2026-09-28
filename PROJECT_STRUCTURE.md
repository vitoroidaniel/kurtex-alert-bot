# Kurtex project structure

## Runtime entry points
- `dashboard.py` - Flask dashboard composition and HTTP routes. Kept as the compatibility entry point for Railway.
- `bot.py` - Telegram bot composition/entry point.

## Backend
- `backend/ai/` - AI chat persistence, maintenance reasoning, research, video/audio processing, and learning/review workflow.
- `backend/core/` - shared application configuration, Chicago-time helpers, dashboard data access, and shift logic.
- `backend/storage/` - case/user persistence adapters.
- `bot_app/handlers/` - Telegram command, alert, agent, report, admin, and scheduler handlers.

## Frontend
- `static/js/core/` - transport/API, shared state, navigation, refresh/bootstrap.
- `static/js/features/` - feature-specific UI: AI, AI learning, reports, case details, analytics, dashboard views, layout, notifications.
- `static/js/platform/` - device/platform behavior such as mobile viewport/navigation.
- `static/css/core/` - main dashboard system and shared icon rules.
- `static/css/features/` - feature-specific styles/fixes.
- `static/css/platform/` - mobile/responsive presentation.
- `static/css/auth/` - login/authentication presentation.
- `static/images/`, `static/vendor/` - assets and vendored libraries.

## Templates and data
- `templates/` - Flask HTML templates and partials.
- `data/` - application knowledge/data files.
- `tests/` - backend and frontend regression tests.

## Dependency rule
Feature code may depend on `backend/core` and `backend/storage`. Core must not depend on feature modules. Bot handlers live outside dashboard/AI modules. Frontend feature code should use `static/js/core/api.js` for HTTP instead of creating separate transports.

## Next architectural boundary
`dashboard.py` remains intentionally compatible with the current deployment entry point. New endpoints should be added as Flask blueprints under `backend/<feature>/routes.py`; existing route groups can be migrated incrementally without another large breaking refactor.
