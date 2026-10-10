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
The leaderboard presents context and period selection, five metrics, ranking with insights, then workload and case activity charts. Panels use 18px gaps and collapse to one column at narrow widths. Allow document scrolling and internal horizontal overflow for filter groups.

Agents uses a muted directory on the left, a selected agent header and five compact all-time statistics on the right, followed by searchable case history. Avg. response is omitted. Keep case history at 20 rows per page with Previous and Next controls. Roles come from the user store. Columns are Driver, Group, Case / Unit, Reported, Status, Actions. The search and select controls occupy a separate aligned toolbar row.

Case Workspace is an additional navigation destination beside Cases. Its compact board uses agent-colored column markers and dense unit cards, with a right-side read-only inspector for details, notes, attachments and history. Blue from the existing `--blue` token marks workspace selection. Alternate Grid, Table and Card views share its independent data and filters. On phones, the inspector appears above the board.

The Chicago clock belongs in the global top bar beside notifications. Use compact time and date text with no calendar tile or large clock card in the page heading.

Shared shell refinements live in `static/css/core/ui-refinements.css`, loaded after legacy styles. One page heading owns contextual print/export/refresh actions; avoid repeating titles inside the page. Controls use a shared 38px height and 9px radius. Body support text and table labels use a readable 12–13px scale without excessive uppercase.

Workspace cards use the reference's compact 132px height and narrow columns, a prominent unit number, up to two issue lines, location, priority, recorded note/attachment counts and date. Desktop opens the first case inspector alongside the board. Selected menu items, agents and cards use a subtle semantic background and one accent edge. Inspector header, tabs and footer remain stable while its body scrolls. On phones, its full-width detail view replaces the board when a case is selected until closed, preserving the board scroll position and filters.

`static/css/core/theme.css` loads last and owns theme compatibility. Legacy `--kx-*` variables alias canonical tokens. `applyTheme` synchronizes the HTML data attribute and legacy body class. Dark Home overrides match the specificity of old page/ID rules so white surfaces cannot win over dark text. The stored theme is applied before CSS loads. Asset revision query parameters keep changed stylesheet/script bundles aligned after updates.

Filter summaries expose the active period/status/search and matching count, with Clear filters. Workspace counts explicitly refer to loaded cases. Search text stays in memory; only view and grouping preferences persist. Mobile navigation gives Cases and Workspace direct access and Agents in More.

## Components
Shared `.toggle-tabs`, `.filter-tabs`, `.notification-tabs`, and `.report-tabs` use inset segmented buttons: selected surface, visible border, bold text, hover and keyboard focus. No extra selected underline. Keep explicit sidebar dividers and remove duplicate group borders.

Charts use horizontal bars so names and counts remain comparable. Reassignment events may overlap case statuses, so do not present those counts as mutually exclusive donut slices. Theme changes update live chart colors. Zero activity has an explicit empty state.

## Accessibility
Use native buttons, visible focus, selected period `aria-pressed`, named charts with numerical text alternatives, reduced motion, and inherited operable scrollbars.
