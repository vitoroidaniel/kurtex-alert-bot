# Dashboard update

- Replaced the green dashboard palette with blue and slate in light/dark modes.
- Reworked profile styling, agent case cards, case detail sections, and the full report view. Closing case details returns to the underlying agent/unit window.
- Highlighted Fleet Status and Intelligence searches with stronger borders and focus treatment.
- Placed a highlighted calendar-style Chicago date and live clock beside Refresh in a sticky header on every tab. Includes the full weekday, month/day, and automatic CST/CDT handling.
- Background polling runs every 15 seconds while the page is visible. Existing DOM nodes are patched instead of clearing and rebuilding the page. Search focus/caret, filters, loaded case pages, and open dialogs remain in place. Detail dialogs remain the snapshot opened by the user; reopen one for updated details.
- Failed background requests keep existing data visible with a connection banner and Retry action.
- Overview settings: show/hide four built-in widgets, choose individual metrics, reorder widgets by drag handles or up/down buttons, select comfortable/compact spacing, and reset defaults. Preferences are scoped to the signed-in account in this browser's local storage, not synced across devices. Arbitrary custom datasets/widgets are not part of this update.

## Installation
Deploy this complete project using the existing workflow. No new production dependency, environment variable, or volume migration is required. Bot handlers, commands, storage, authentication, and backend API code were not changed.

## Validation
JavaScript syntax and Python compilation passed. The authenticated Flask template rendered successfully. DOM integration checks with synthetic data passed for polling, preserved input identity/focus/caret, layered dialogs, saved layout settings, pagination, API error recovery, rapid tab navigation, and keyed node reconciliation. Browser screenshot and physical drag/scroll checks could not run because the browser download was unavailable in the test environment. Production data and deployment were not accessed.

## v27
- Removed Truck 3D Lab navigation/page.
- Reworked Parts Manual into the primary mechanical reference workspace.
- Preserved Serper live real-part photos and backend-only API key.
- Improved desktop/mobile hierarchy, photo gallery, part index and troubleshooting cards.

## v32
- Compact 3x2 overview/fleet metric cards.
- Compact Fleet Intelligence stats and Search by Issue.
- Fixed All/Truck/Trailer/Reefer active tab highlight alignment.
- Neutral search field with red focus state only.
