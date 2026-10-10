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
Use Inter with Arial fallback. Page context uses 20px headings, panel titles 15px, readable supporting copy 12px, and prominent numerical metrics. Chart labels inherit the theme and use tabular integer scales.

## Layout
Home is an operations overview for fleet staff. A personal time-of-day greeting and date use the Chicago operational timezone; a quiet amber date adds warmth to the open, unboxed welcome row. Four compact, permission-aware action buttons lead to the existing maintenance form, Workspace, fleet search and AI chat. Four metric tiles pair a semantic icon with a count and label, plus a separated comparison/context footer. These remain clickable and individually hideable.

The default widget order is metrics, attention queue, fleet briefing, recent updates, team progress, recurring units and optional recent cases. Desktop uses a wider work column and a quieter right column: attention alongside briefing, then activity alongside team progress. Metrics, recurring units and recent cases span the full grid. Briefing uses readable separated rows instead of cards nested inside a card. Unit reports use up to six native button tiles with real counts and existing unit-detail actions. Mobile stacks panels, keeps two-column metric tiles and compact action buttons, and retains the same content. There are no forced list minimum heights or decorative empty panels.

`static/css/features/home.css` loads after chrome and owns only Home's variant. It consumes canonical surface, text, border, accent and status tokens. Inter remains the shared typeface; greeting uses 28px display text, metrics use 28px numerals, panel titles use 15px and operational rows use 12-13px text. Cards use shared 12px rounding and 18px desktop gaps. Customize Home owns ordering and hiding. Former default layouts migrate to the new order; custom orders and visibility stay intact. Background refresh preserves focused list actions. No simulated activity, invented trends or decorative live indicators.

The leaderboard begins directly with five compact metrics. A contained performance report follows the metrics: its toolbar contains period and rank-by filters, with a comparable ranking table beside team insights and two workload/activity charts underneath. Panels use 16px gaps and collapse at narrow widths. Ranking retains its existing calculation and paging; the table exposes handled, resolved, active, missed and reassigned counts independently.

Kurtex Intelligence starts with four summary cards: active findings, critical findings, affected units and supporting evidence. The findings toolbar and filtered finding cards follow. Filters use pressed state; refresh has pending and inline error states, preserving previous findings when a read fails. Do not place filters above the summary cards.

Parts Manual starts below the same shared header with a search panel and visible category buttons. Category buttons wrap on desktop and scroll horizontally on phones; they expose selected state. Remove the obsolete total-parts badge and hide a zero result-count label. Reference body text is 13px, search text 14px, and part names 13px. Parts content uses natural document scrolling. The shared header, controls and detail content remain in document flow; neither body nor page uses the obsolete full-screen scroll lock. Only the bounded parts index scrolls internally, and wheel movement chains back to the page at its edges. Detail content has no separate vertical scrollbar or clipping height. Existing modal scroll locks remain authoritative.

My Profile uses a compact identity rail and existing appearance, notification and interface settings shortcuts. Four all-time metric cards, today/week activity and recent cases form the main column. Search and status controls filter the ten recent cases returned by the existing profile API; the limit is explicit. The table uses Driver, Group, Case / Unit, Reported, Status and Actions. Preserve account identity and permissions; this screen introduces no profile editing or account writes. Period resolution counts describe the current status of cases opened during each period, not closures that occurred during that period. All surfaces inherit light/dark tokens.

Agents uses a muted directory on the left, a selected agent header and five compact all-time statistics on the right, followed by searchable case history. Avg. response is omitted. Keep case history at 20 rows per page with Previous and Next controls. Roles come from the user store. Columns are Driver, Group, Case / Unit, Reported, Status, Actions. The search and select controls occupy a separate aligned toolbar row.

Case Workspace remains an additional destination beside Cases. It contains active reported maintenance cases, excluding ordinary intake and completed reports. The supplied October 10 reference owns its visual direction: pale contained columns with agent markers, priority-colored card edges, a compact toolbar and a right inspector. `static/css/features/workspace.css` owns this business variant. Its action token is `--cw-action: #245ef4` in light mode and `#2868da` in dark mode; shared surface, text and semantic tokens remain canonical. Grid, Table and Card views use the same reports and filters. A dedicated full-case page presents description, activity, notes and details.

The full Workspace case record uses 23px issue headings, 16px section headings, 15px body copy and 14px timeline/detail values. The left column contains description, timeline, Notes & comments and then attachments. A single right-hand panel groups Edit report, Add note and Close case above the case details, with one concise retention explanation; remove duplicate actions in section headings. Related active reports follow in the right column. Narrow layouts show the issue first, actions/details next, then the timeline, notes and attachments. Preserve existing report permissions, confirmation and durable lifecycle; this redesign adds no reopening, deletion, file downloads or Telegram dispatch.

The sidebar footer keeps the account identity above one row of three matching 40px icon buttons for theme, settings and sign out. All three use the same border, surface and rounding, with named actions, title tooltips and visible keyboard focus. The theme icon and accessible action follow the stored theme through the canonical theme handler. Compact desktop navigation stacks those same buttons vertically. Settings and sign out retain their existing dialogs.

The Chicago clock belongs in the shared top bar beside notifications, except Workspace whose top bar contains only its title. Use compact time and date text with no calendar tile or large clock card in the page heading.

`static/css/core/chrome.css` follows the shared theme stylesheet and owns the compact shell, leaderboard presentation and notification panel. Home's scoped variant follows chrome. Missed Cases is the baseline for the shared shell. Every tab uses the same sidebar, 28px desktop gutters, 16px phone gutters, 64px top bar and 20px desktop content gap. Workspace must never override sidebar width, navigation colors or main padding. Remove duplicate page-heading rows and descriptive hero copy from operational tabs; a single title provides page context without a KURTEX breadcrumb. The top bar and mobile header remain in document flow and scroll away. Home exposes Report, Print, Export and Refresh in that bar. Operational tabs share title, clock and notifications without page action buttons. Workspace has only Case Workspace in the top bar. Home keeps its welcome layout and Report, Print, Export and Refresh. Agents starts directly with its directory and selected-agent features. Controls and the Chicago clock have explicit spacing so time, date and connection state do not run together.

Notifications use a compact anchored panel with a single scrolling list, restrained unread markers, semantic alert icons and native buttons for case links. All, Unread and Alerts filters show counts only when updates exist; an empty inbox hides the filters and footer actions. Mark all read appears only with unread updates. Loading, unavailable, retry and cached-data states have explicit copy. Header actions mark all read and close; clearing belongs in the footer. All colors derive from existing theme tokens. Escape returns focus to the bell. Failed read requests retain unread state and report the failure. Local case notifications, snapshots and dismissed IDs are scoped to the current account; repeated local events are suppressed.

Workspace cards use a compact minimum 108px desktop height, 230px columns and priority edges. Unit, up to two issue lines, location and one footer row of priority, recorded note/file counts and reported date match the reference. Cards grow for wrapped content. Column footers offer Add case. Board and inspector share a bounded viewport height inside one contained work area. Mouse drag pans horizontally after a movement threshold; a click still opens a case. Arrow buttons, keyboard arrows, native trackpad movement and Shift + scroll provide alternatives. Vertical scrolling inside a column takes priority. Desktop opens the first inspector once. Inspector header, tabs and footer stay stable while its body scrolls. On phones, the inspector replaces the board until dismissed and the full case page stacks its panels with actions available. A confirmed Close case marks Done, removes the report from Workspace and preserves history in Cases. New report, edit and note forms reuse the shared modal shell and select controls.

`static/css/core/theme.css` owns theme compatibility; the later chrome stylesheet uses those tokens for geometry and component variants. Legacy `--kx-*` variables alias canonical tokens. `applyTheme` synchronizes the HTML data attribute and legacy body class. Dark Home overrides match the specificity of old page/ID rules so white surfaces cannot win over dark text. The stored theme is applied before CSS loads. Asset revision query parameters keep changed stylesheet/script bundles aligned after updates.

Missed Cases places its search inside a results panel below the shared Missed Cases title, with no duplicate page heading. Case history uses one contained panel: search and period/status/date controls share an aligned toolbar that wraps at smaller widths, with its summary immediately above the table. Filter summaries expose the active period/status/search and matching count, with Clear filters. Workspace counts explicitly refer to loaded cases. Search text stays in memory; only view and grouping preferences persist. Mobile navigation gives Cases and Workspace direct access and Agents in More.

## Components
Shared `.toggle-tabs`, `.filter-tabs`, `.notification-tabs`, and `.report-tabs` use inset segmented buttons: selected surface, visible border, bold text, hover and keyboard focus. No extra selected underline. Keep explicit sidebar dividers and remove duplicate group borders.

Charts use horizontal bars so names and counts remain comparable. Reassignment events may overlap case statuses, so do not present those counts as mutually exclusive donut slices. Theme changes update live chart colors. Zero activity has an explicit empty state.

## Accessibility
Use native buttons, visible focus, selected period `aria-pressed`, named charts with numerical text alternatives, reduced motion, and inherited operable scrollbars.
