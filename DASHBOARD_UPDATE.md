# Dashboard Update v34

- Reworked Agents into compact master/detail workspace.
- Added Parts ↔ Cases using actual case text matches.
- Added Recurring Problems based on repeated unit/problem patterns in stored cases.
- Added Knowledge Notes persisted in DATA_DIR/knowledge_notes.json.
- Added Case Similarity using historical case text overlap and same-unit boost.
- Existing Parts Manual, Serper search, bot functionality and dashboard features preserved.

## v36 hotfix
- Fixed `apiFetch` so POST/PUT/DELETE request options are actually passed to `fetch`. This was the root cause of Knowledge Notes add/edit/delete not working in v35.
- Fixed agent card click markup to use escaped `data-*` attributes rather than embedding JSON strings inside an HTML `onclick` attribute.
- Preserved the redesigned card-based Agents layout and existing dashboard features.
- Static validation completed with `node --check` for modified JavaScript and `py_compile` for `dashboard.py`.
