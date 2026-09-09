# AI Project Finder

AI Project Finder is the public, portable source for a local cross-AI session
index covering Codex, Claude Code, Kimi Code, compatible Kimi Desktop Work,
WorkBuddy, Qwen Work, locally cached Doubao Work conversations, and DeepSeek
Harness records. `README.md` and `README.zh-CN.md` are the user-facing contracts.

## Run and verify

```bash
python3 app.py --open
python3 app.py --demo
python3 -m py_compile app.py
python3 -m unittest discover -s tests
```

`python3 app.py --build-only` reads supported histories on the current computer
and rewrites the ignored private index at `data/index.json`. Use Demo Mode for
synthetic or privacy-safe visual verification.

## Stack and structure

- `app.py`: Python standard-library server, original adapters, indexer, and open actions.
- `search_index.py`: local normalized substring search, revision-scoped result cache, paged summaries and text.
- `/api/index` returns metadata only; `/api/search` and `/api/record` return bounded pages. Revisions reject stale pages.
- `desktop_sources.py`: read-only desktop/Harness adapters; `vendor/chromium/` is a pinned MIT IndexedDB decoder subset.
- Harness `.zstd` needs Python 3.14 stdlib, optional `zstandard`, or a `zstd` CLI.
- Source tests must disable all unneeded adapters, including future ones; never scan real user histories in a unit test.
- `static/index.html`: self-contained localized interface; vendored assets live under `static/vendor/`.
- `config.json`: portable defaults and source discovery; English is the repository default.
- `demo/`: synthetic fixtures and the demo recording script.
- `tests/`: cross-platform parser, privacy, Demo Mode, and HTTP tests.
- `scripts/build_release.py`: deterministic English/Chinese macOS/Windows packaging.
- `outputs/`, `backups/`, generated indexes, the parse cache, project assignments,
  logs, and manual traces are local and ignored.
- `/api/` requires the per-launch session token, a loopback `Host`, and a same-origin
  POST. Changing the handler means re-checking `LocalApiBoundaryTest`.

## Boundaries

- Keep the public source free of usernames, absolute personal paths, real sessions,
  client data, tokens, and generated indexes.
- Treat source histories as read-only. Runtime writes belong only under `data/`.
- Keep private installed copies and personalized configuration outside this public
  repository unless a task explicitly includes migration or compatibility work.
- Interface edits require a backup plus HTML tag and JavaScript syntax checks.
- Release changes use a branch and PR, pass macOS and Windows CI, and build all four
  archives from one exact commit with recorded SHA256 checksums.

## Current state

The current public release is v1.4.0: English and Simplified Chinese editions
for macOS and Windows. `APP_VERSION` in `app.py` is the single source for the
release number; the README status lines and download links must agree with it,
and `VersionConsistencyTest` fails when they drift.

v1.4.0 includes the September source expansion, PR #12's full-prompt recall,
and local server search with paged result snippets and on-demand indexed text.
New macOS sources are live-verified; Windows source discovery has synthetic-test
coverage only. The local service still retains the full saved index in memory.
