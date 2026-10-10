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
| Agent activity presentation | `static/js/features/agents-v23.js` | Five compact metrics without average response; columns Driver, Group, Case / Unit, Reported, Status, Actions. Clear search returns focus to its input. |
| Case Workspace | `static/js/features/case-workspace.js` | Additional Cases-authorized destination with independent list state and filters. Inline inspector uses the authenticated case detail API. No case mutations introduced. |
| Global clock | `static/js/core/app.js` | Preserve America/Chicago time, date, and connection status in the main top bar. |
| Shell and filter summaries | `static/js/features/ui-refinements.js`, `static/css/core/ui-refinements.css` | Single page heading, contextual actions, visible selected navigation and filter reset. Search queries remain in memory; workspace view/group are safe stored preferences. Counts distinguish loaded matches from the full dataset. |
| Mobile workspace inspection | `static/js/features/case-workspace.js` | Non-modal full-width in-page inspector replaces board content on phones; close restores the matching case button and page scroll. Filters stay intact. Header and actions remain outside the scrolling detail body. |
| Theme compatibility | `static/css/core/theme.css`, `static/js/core/state.js` | HTML data attribute and legacy body class stay synchronized; high-specificity Home surfaces follow canonical dark tokens. Desktop workspace opens its first available case once; phones wait for a selection. |

Existing forms, CRUD, permissions, notifications and lifecycle behavior remain outside this visual change. Verify leaderboard period switching, rank paging, empty charts, night/light switching, keyboard focus and phone overflow.
