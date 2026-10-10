# UI ownership

Scope: shared filter presentation, navigation dividers, leaderboard rendering and theme consistency. Domain rules remain owned by Flask API routes in `dashboard.py`; this change does not alter them. Product context: `README.md` and existing templates.

| Capability | Canonical owner | Contract |
| --- | --- | --- |
| Period filters | `static/js/core/state.js` | Existing period handlers retain API semantics; leaderboard period changes reset ranking pagination and expose pressed state. |
| Filter presentation | `static/css/core/kurtex-v23.css` | One shared segmented style across existing filter groups. |
| Select/Listbox | `static/js/core/custom-select.js` | Preserve existing shared select enhancement; no replacement introduced. |
| Scrollbar | `static/css/core/kurtex-v23.css` | Global inherited theme styling, with system forced colors. |
| Leaderboard | `static/js/features/views.js` | Preserve ranking/filter calculations, show empty charts honestly, redraw colors on theme changes. |
| Theme | `static/js/core/state.js`, dashboard CSS tokens | Stored preference controls document theme; legacy aliases resolve to canonical surfaces. |
| Agent directory | `static/js/features/agents-v23.js`, `/api/agents`, `/api/agent` | Server-owned identity, role, statistics, status/search filters and chronological sort; 20 cases per page. Ignore stale responses when selecting another agent. |
| Case Workspace | `static/js/features/case-workspace.js` | Additional Cases-authorized destination with independent list state and filters. Inline inspector uses the authenticated case detail API. No case mutations introduced. |
| Global clock | `static/js/core/app.js` | Preserve America/Chicago time, date, and connection status in the main top bar. |

Existing forms, CRUD, permissions, notifications and lifecycle behavior remain outside this visual change. Verify leaderboard period switching, rank paging, empty charts, night/light switching, keyboard focus and phone overflow.
