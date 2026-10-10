# Kurtex UX Contract (initial)

- Existing dashboard routes, bot actions, case status transitions, exports and AI endpoints are authoritative and unchanged.
- Desktop sidebar navigation remains single-page; keyboard Enter and Space activate role-button navigation.
- Mobile navigation retains existing tab layout and more-menu.
- Report, case, and notification flows retain their existing owning JavaScript handlers.
- Loading, empty, error, and destructive action patterns require dedicated end-to-end validation before any redesign.
- Accessibility target WCAG 2.2 AA; keyboard focus visible; reduced motion supported.
- Shared color and spacing rules are centralized in `static/css/core/kurtex-refinement.css` for this incremental phase.

## V3 navigation behavior
Preserve all existing role-based nav visibility, showPage handlers, group expansion, account controls and filter selection semantics. New sidebar labels are noninteractive. Mobile uses existing drawer behavior.

## V4 mobile contract
Do not remove or rename existing page identifiers, filter handlers, or Telegram/backend APIs. Mobile filter groups scroll horizontally without clipping actions. Sidebar remains scrollable and keyboard focus visible. Charts shrink in height on mobile and never force horizontal page scrolling. Reduced-motion settings are respected.

## V5 workflow contract
Mobile case filters retain setCaseFilter, loadCases and setCaseDateFilter. More-menu destinations are hidden when not allowed. The Alerts button uses the existing notification renderer. AI retains its existing chat and composer logic. No API or database migration is needed.
