# Kurtex Design System

## Direction
Operational clarity with restrained industrial polish. Preserve existing product behavior and established feature layouts.

## Canonical runtime tokens
The original tokens live in `static/css/core/dashboard.css`; the incremental visual refinement lives in `static/css/core/kurtex-refinement.css`, loaded after existing styles. Consolidate tokens only after regression testing.

- Background `#f3f6fa`, surface `#ffffff`, text `#182b42`, accent `#3575c5`, border `#dce4ed`.
- Sidebar `#182b42`, selected `#34547a`.
- Body typography: existing Inter. Compact data-first density, rounded 14px cards, restrained shadow.
- Dark mode uses independent surface and text tokens; never use status color as decorative background.
- Respect reduced motion and keyboard focus.

## Scope of first pass
Shared dashboard surfaces, sidebar navigation, visual tokens, keyboard navigation, mobile overflow guard. No API or domain logic changes.

## V3 navigation and components
Light neutral workspace sidebar with blue active indicators, sectional navigation labels, compact status-first cards and consistent segmented filter controls. Keep existing route permissions and event handlers. Reference is inspiration, not a pixel clone.

## V4 shared UI
Shared filter controls use a unified 40px minimum mobile touch area, focus-visible rings, scrollable segmented controls, and consistent spacing. Agent cards and leaderboard prioritize data legibility at 320px-760px. The established blue active navigation and light workspace identity are preserved.

## V5 mobile navigation and surfaces
The primary bottom bar is Cases / Search / AI / Alerts / More. Secondary tools live in a role-aware More sheet. Cases filter controls use a bottom sheet, while existing filter callbacks and backend APIs remain canonical. Notification center is moved to a body-level portal on phones so it is not hidden by the desktop header.

## V6 corrections
The navigation is a fixed-height shell with an independently scrolling nav list and fixed footer. Desktop supports persisted icon-only compact mode. Mobile uses a separate drawer with its own scrolling. Page content respects the available width; narrow parts and knowledge screens stack.

## V7 component rebuild
Agent card HTML is replaced by a centered employee grid with avatar, status, metrics and progress. Leaderboard charts use refined series colors, rounded bars, and unstacked comparisons. Mobile uses two columns when possible and one on narrow phones. Existing click handlers and data fields are retained.

## V8 sidebar and headers
Navigation sections are tightly grouped, the sidebar scroll indicator is hidden, collapsed group items are not rendered in compact mode, and page heading banners use a clean surface rather than oversized gray backgrounds.

## V9 corrections
Neutral page headers across tabs, quieter sidebar active states, more expressive left-aligned agent cards, five-column leaderboard metric layout, horizontal per-agent outcome chart.

V11: Selected filter states use a solid blue surface and white text; inactive options remain neutral. Sidebar account buttons are compact while the avatar and role have increased prominence.
