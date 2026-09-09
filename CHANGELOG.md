# v1.4.0 · 2026-09-09

Based on GitHub main `616fe07`, including [PR #12](https://github.com/stevensilu/ai-project-finder/pull/12) by ShinXinyuChen, merged August 3, 2026. That PR preserves late user turns, fixes excerpt highlighting and ranking, and compresses the index response.

- Add WorkBuddy task discovery, prompt cleaning, current metadata and exact task links.
- Add Qwen Work CN desktop tasks through read-only SQLite snapshots, with WAL-aware refresh and exact task/sub-chat links.
- Add Doubao Work cached-conversation discovery with a pinned MIT IndexedDB reader, latest live-key selection, revision deduplication and explicit partial-coverage status.
- Add DeepSeek Harness plain/Zstandard session logs, main-session filtering and a configurable loopback Web UI address.
- Show all supported sources and readable diagnostics, including detected-but-empty sources.
- Fix native-labeled manual traces, disabled-source configuration, numeric-string timestamps, overlapping roots and source-scoped parse cache keys.
- Run search locally in Python; load 30 results per page, bounded match snippets, and 12,000-character text segments on demand.
- Page project groups and their session previews; preserve Chinese substrings, normalized Unicode phrases, every query term, source/date filters and metadata-weighted ranking.
- Tie pages to an immutable index revision and reject stale pages; cancel obsolete browser searches. Fetch original manual notes for editing.
- Return metadata from `/api/index`, paged results from `/api/search` and paged text from `/api/record`; retain existing local API protections.
- Show the running version in the footer.
- Add synthetic examples and regression checks for the new sources.

## Remaining limits and next opportunities

- Doubao coverage is bounded by what its desktop cache retains. A supported export or client API would provide more complete history. Opening a task in the client and refreshing may populate more cache; this is not guaranteed.
- Harness and Doubao currently open their application entrypoints. Stable conversation-level deep links should replace this when their clients expose them.
- Qwen's desktop database represents UI tasks. Background awareness JSONL files are deliberately outside this adapter's scope.
- The Python service still retains the full local index and a normalized search copy in memory. Paging resolves browser transfer/rendering growth; the unbounded on-disk/session growth discussed in [issue #13](https://github.com/stevensilu/ai-project-finder/issues/13) remains a separate concern. A persistent disk search index could reduce service memory while preserving late-turn recall.
- Artifact clues currently come mainly from user prompts. Structured assistant file outputs could improve final-deliverable recall, with tool-result noise excluded.
- Signed application packages and an update command that preserves private configuration/data would simplify future upgrades.
