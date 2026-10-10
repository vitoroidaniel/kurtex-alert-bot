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

Agents uses a muted directory on the left, a selected agent header and six all-time statistics on the right, followed by searchable case history. Keep case history at 20 rows per page with Previous and Next controls. Roles come from the user store.

Case Workspace is an additional navigation destination beside Cases. Its compact board uses agent-colored column markers and dense unit cards, with a right-side read-only inspector for details, notes, attachments and history. Blue from the existing `--blue` token marks workspace selection. Alternate Grid, Table and Card views share its independent data and filters. On phones, the inspector appears above the board.

The Chicago clock belongs in the global top bar beside notifications. Use compact time and date text with no calendar tile or large clock card in the page heading.

## Components
Shared `.toggle-tabs`, `.filter-tabs`, `.notification-tabs`, and `.report-tabs` use inset segmented buttons: selected surface, visible border, bold text, hover and keyboard focus. No extra selected underline. Keep explicit sidebar dividers and remove duplicate group borders.

Charts use horizontal bars so names and counts remain comparable. Reassignment events may overlap case statuses, so do not present those counts as mutually exclusive donut slices. Theme changes update live chart colors. Zero activity has an explicit empty state.

## Accessibility
Use native buttons, visible focus, selected period `aria-pressed`, named charts with numerical text alternatives, reduced motion, and inherited operable scrollbars.
