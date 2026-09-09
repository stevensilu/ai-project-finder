Vendored read-only IndexedDB subset (MIT).

- https://github.com/cclgroupltd/ccl_chromium_reader at ef840de30221c4d65bc96d2f4d9057e9ef2f526d
- https://github.com/cclgroupltd/ccl_simplesnappy at 3d085230baa8c46cf2090ebba29bf6e8eab31087

Only package imports were changed to relative imports; unrelated browser stores and package initializers are excluded. No network calls or browser credential access. Application-level live-record selection is in desktop_sources.py.
