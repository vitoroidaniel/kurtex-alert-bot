---
version: alpha
name: Kurtex Fleet Operations
description: A compact operations dashboard for case activity and agent performance.
colors:
  primary: "#d62828"
  background: "#f3f5fa"
  nightSurface: "#171e29"
typography:
  sans:
    fontFamily: "Inter, Arial, sans-serif"
rounded:
  DEFAULT: "12px"
spacing:
  panel-gap: "18px"
components:
  filter: {}
  chart: {}
---

# Kurtex design system

## Overview
Product interface for fleet operators comparing maintenance cases and agent activity. English UI, with responsive desktop and phone layouts. Preserve the existing restrained red identity and compact Inter typography; no marketing decoration.

## Colors
Runtime tokens in `static/css/core/dashboard.css` are canonical. The final shared theme adapters in `static/css/core/kurtex-v23.css` map legacy `--card`, `--soft`, `--surface-2`, and text aliases onto those tokens. Night surfaces use navy charcoal with light text; every panel must inherit theme tokens rather than a white fallback.

## Typography
Use Inter with Arial fallback. Page context uses 21px headings, panel titles 15px, readable supporting copy 12px, and prominent numerical metrics. Chart labels inherit the theme and use tabular integer scales.

## Layout
The leaderboard begins directly with five compact metrics. Period and rank-by filters sit underneath, followed by a comparable ranking table beside team insights and two workload/activity charts. Panels use 16px gaps and collapse at narrow widths. Ranking retains its existing calculation and paging; the table exposes handled, resolved, active, missed and reassigned counts independently.

Agents uses a muted directory on the left, a selected agent header and five compact all-time statistics on the right, followed by searchable case history. Avg. response is omitted. Keep case history at 20 rows per page with Previous and Next controls. Roles come from the user store. Columns are Driver, Group, Case / Unit, Reported, Status, Actions. The search and select controls occupy a separate aligned toolbar row.

Case Workspace remains an additional destination beside Cases. It contains active reported maintenance cases, excluding ordinary intake and completed reports. The supplied October 10 reference owns its visual direction: pale contained columns with agent markers, priority-colored card edges, a compact toolbar and a right inspector. `static/css/features/workspace.css` owns this business variant. Its action token is `--cw-action: #245ef4` in light mode and `#2868da` in dark mode; shared surface, text and semantic tokens remain canonical. Grid, Table and Card views use the same reports and filters. A dedicated full-case page presents description, activity, notes and details.

The Chicago clock belongs in the global top bar beside notifications. Use compact time and date text with no calendar tile or large clock card in the page heading.

`static/css/core/chrome.css` loads last and owns the compact shell, leaderboard presentation and notification panel. Remove the shared page-heading row and descriptive hero copy from operational tabs; the small top-bar breadcrumb provides page context. The top bar and mobile header remain in document flow and scroll away. Home exposes Report, Print, Export and Refresh in that bar. Agents has only its directory and selected-agent features, without a redundant heading or manual Refresh action. Controls and the Chicago clock have explicit spacing so time, date and connection state do not run together.

Notifications use a compact anchored panel with All, Unread and Alerts filters, a single scrolling list, restrained unread markers, semantic alert icons and native buttons for case links. Header actions mark all read and close; clearing belongs in the footer. All colors derive from existing theme tokens. Escape returns focus to the bell. A failed mark-all request retains unread state and reports the failure.

Workspace cards use a compact minimum 108px desktop height, 230px columns and priority edges. Unit, up to two issue lines, location and one footer row of priority, recorded note/file counts and reported date match the reference. Cards grow for wrapped content. Column footers offer Add case. Desktop opens the first inspector once. Inspector header, tabs and footer stay stable while its body scrolls. On phones, the inspector replaces the board until dismissed and the full case page stacks its panels with actions available. A confirmed Close case marks Done, removes the report from Workspace and preserves history in Cases. New report, edit and note forms reuse the shared modal shell and select controls.

`static/css/core/theme.css` owns theme compatibility; the later chrome stylesheet uses those tokens for geometry and component variants. Legacy `--kx-*` variables alias canonical tokens. `applyTheme` synchronizes the HTML data attribute and legacy body class. Dark Home overrides match the specificity of old page/ID rules so white surfaces cannot win over dark text. The stored theme is applied before CSS loads. Asset revision query parameters keep changed stylesheet/script bundles aligned after updates.

Filter summaries expose the active period/status/search and matching count, with Clear filters. Workspace counts explicitly refer to loaded cases. Search text stays in memory; only view and grouping preferences persist. Mobile navigation gives Cases and Workspace direct access and Agents in More.

## Components
Shared `.toggle-tabs`, `.filter-tabs`, `.notification-tabs`, and `.report-tabs` use inset segmented buttons: selected surface, visible border, bold text, hover and keyboard focus. No extra selected underline. Keep explicit sidebar dividers and remove duplicate group borders.

Charts use horizontal bars so names and counts remain comparable. Reassignment events may overlap case statuses, so do not present those counts as mutually exclusive donut slices. Theme changes update live chart colors. Zero activity has an explicit empty state.

## Accessibility
Use native buttons, visible focus, selected period `aria-pressed`, named charts with numerical text alternatives, reduced motion, and inherited operable scrollbars.
