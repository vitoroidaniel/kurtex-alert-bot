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
