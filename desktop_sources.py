"""Read-only adapters for desktop and local web agents.

Return plain metadata; app.py owns cleaning, project naming and the public API.
No credentials, browser processes or remote endpoints are accessed here.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

LABELS = {"workbuddy": "WorkBuddy", "qwenwork": "Qwen Work", "doubao-work": "Doubao Work", "deepseek-harness": "DeepSeek Harness"}


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(content_text(v) for v in value if isinstance(v, (dict, str)))
    if isinstance(value, Mapping):
        kind = value.get("type")
        if kind in {"text", "input_text", "output_text"}:
            return str(value.get("text") or "")
        # Doubao's content blocks wrap user-authored text in text_block.
        if "text_block" in value:
            return str(value["text_block"].get("text") or "")
        if "content" in value:
            return content_text(value["content"])
    return ""


def json_value(value: Any, fallback: Any) -> Any:
    if not isinstance(value, str):
        return value if value is not None else fallback
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return fallback


def clean_prompt(text: str) -> str:
    # Strip runtime envelopes BEFORE the per-request ceiling is applied.
    return re.sub(r"<(system-reminder|identity_context|environment_context|system_context)(?:\s[^>]*)?>[\s\S]*?</\1>", " ", text, flags=re.I).strip()


def source_files(source: str, root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
    elif source == "workbuddy":
        base = root / "projects" if (root / "projects").is_dir() else root
        yield from (p for p in base.rglob("*.jsonl") if "subagents" not in p.parts and not p.stem.startswith("agent-"))
    elif source == "qwenwork":
        if (root / "data" / "agents.db").is_file():
            yield root / "data" / "agents.db"
        elif (root / "agents.db").is_file():
            yield root / "agents.db"
    elif source == "deepseek-harness":
        base = root / "sessions" if (root / "sessions").is_dir() else root
        yield from base.rglob("session.jsonl")
        yield from base.rglob("session.jsonl.zstd")
    elif source == "doubao-work":
        if root.name.endswith(".indexeddb.leveldb"):
            yield root
        else:
            # Only Doubao Work conversation stores, never other Chromium origins.
            for name in ("chrome_doubaowork-chat_0.indexeddb.leveldb", "chrome_doubaowork-launcher_0.indexeddb.leveldb"):
                yield from root.glob("Default/IndexedDB/" + name)
                yield from root.glob("Profile */IndexedDB/" + name)


def workbuddy_database(path: Path) -> Path | None:
    for parent in path.parents:
        if parent.name == "projects":
            db = parent.parent / "workbuddy.db"
            return db if db.is_file() else None
    return None


def fingerprint(source: str, path: Path) -> list[Any]:
    files = [path]
    if path.is_dir():
        files = [p for p in path.iterdir() if p.is_file() and (p.suffix in {".ldb", ".sst", ".log"} or p.name == "CURRENT" or p.name.startswith("MANIFEST-"))]
        blobs = path.with_name(path.name.replace(".leveldb", ".blob"))
        if blobs.exists():
            files.extend(p for p in blobs.rglob("*") if p.is_file())
    db = path if source == "qwenwork" else workbuddy_database(path) if source == "workbuddy" else None
    if db:
        files.extend([db, Path(str(db) + "-wal")])
    return [[str(p), p.stat().st_mtime_ns, p.stat().st_size] for p in sorted(set(files)) if p.exists()]


@contextmanager
def readonly_database(path: Path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")  # A consistent snapshot, including committed WAL rows.
        yield db
    finally:
        db.close()


def read_workbuddy(path: Path) -> list[dict[str, Any]]:
    session_id, cwd, title = path.stem, "", ""
    prompts, seen, created, updated = [], set(), None, None
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            row = json_value(line, {})
            if not isinstance(row, dict):
                continue
            session_id = str(row.get("sessionId") or session_id)
            cwd = str(row.get("cwd") or cwd)
            stamp = row.get("timestamp")
            if stamp:
                created = created or stamp
                updated = stamp
            if row.get("type") in {"ai-title", "custom-title"}:
                title = str(row.get("customTitle") or row.get("aiTitle") or title)
            if row.get("type") == "message" and row.get("role") == "user":
                key = row.get("id")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                text = clean_prompt(content_text(row.get("content")))
                if text:
                    prompts.append(text)
    db_path = workbuddy_database(path)
    if db_path:
        with readonly_database(db_path) as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row:
                metadata = dict(row)
                if metadata.get("deleted_at") or metadata.get("is_background_automation"):
                    return []
                title = str(metadata.get("custom_title") or metadata.get("title") or title)
                cwd = str(metadata.get("cwd") or cwd)
                created = metadata.get("created_at") or created
                updated = metadata.get("last_activity_at") or metadata.get("updated_at") or updated
    if not prompts:
        return []
    return [{"session_id": session_id, "cwd": cwd, "title": title, "prompts": prompts, "created_at": created, "updated_at": updated,
             "managed_workspace": bool(re.search(r"[/\\]WorkBuddy[/\\]\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$", cwd))}]


def read_qwenwork(path: Path) -> list[dict[str, Any]]:
    results = []
    with readonly_database(path) as db:
        chats = {r["id"]: dict(r) for r in db.execute("SELECT * FROM chats")}
        projects = {r["id"]: dict(r) for r in db.execute("SELECT * FROM projects")}
        has_messages = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'").fetchone()
        for sub in db.execute("SELECT * FROM sub_chats"):
            sub = dict(sub)
            chat = chats.get(sub["chat_id"], {})
            if not chat or chat.get("deleted_at"):
                continue
            rows = list(db.execute("SELECT * FROM messages WHERE sub_chat_id=? AND role='user' ORDER BY sequence", (sub["id"],))) if has_messages else []
            if rows:
                prompts = [clean_prompt(content_text(json_value(r["parts"], [])) or r["searchable_text"] or "") for r in rows]
            else:
                legacy = json_value(sub.get("messages"), [])
                prompts = [clean_prompt(content_text(r.get("parts", r.get("content", [])))) for r in legacy if isinstance(r, dict) and r.get("role") == "user"] if isinstance(legacy, list) else []
            prompts = [p for p in prompts if p]
            if not prompts:
                continue
            cwd = str(chat.get("worktree_path") or projects.get(chat.get("project_id"), {}).get("path") or "")
            results.append({"session_id": str(sub.get("session_id") or sub["id"]), "chat_id": chat["id"], "sub_chat_id": sub["id"],
                            "title": str(chat.get("name") or sub.get("name") or ""), "cwd": cwd, "prompts": prompts,
                            "created_at": chat.get("created_at"), "updated_at": sub.get("updated_at") or chat.get("updated_at"),
                            "managed_workspace": bool(re.search(r"[/\\]\.qwenworkcn[/\\]workspace[/\\][^/\\]+$", cwd))})
    return results


@contextmanager
def transcript_stream(path: Path):
    if path.suffix != ".zstd":
        with path.open(encoding="utf-8", errors="replace") as stream:
            yield stream
        return
    try:
        from compression import zstd
    except ImportError:
        try:
            import zstandard
        except ImportError:
            binary = shutil.which("zstd")
            if not binary:
                raise RuntimeError("Zstd support unavailable; use Python 3.14+, or install zstandard")
            process = subprocess.Popen([binary, "-dc", "--", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                assert process.stdout is not None
                with io.TextIOWrapper(process.stdout, encoding="utf-8", errors="replace") as stream:
                    yield stream
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
            return
        with path.open("rb") as source, zstandard.ZstdDecompressor().stream_reader(source) as reader, io.TextIOWrapper(reader, encoding="utf-8", errors="replace") as stream:
            yield stream
    else:
        with zstd.open(path, "rt", encoding="utf-8", errors="replace") as stream:
            yield stream


def read_deepseek(path: Path) -> list[dict[str, Any]]:
    header, prompts, title, updated, seen = {}, [], "", None, set()
    with transcript_stream(path) as stream:
        for line in stream:
            row = json_value(line, {})
            if not isinstance(row, dict):
                continue
            kind = row.get("type")
            if kind == "session":
                header = row
                if row.get("parentSession") or row.get("delegationDepth", 0) > 0:
                    return []
            updated = row.get("time") or updated
            data = row.get("data", {})
            if kind == "session/title":
                title = str(data.get("title") or "") if isinstance(data, dict) else str(data)
            if kind == "user/message" and isinstance(data, dict):
                origin = data.get("source", {})
                if isinstance(origin, dict) and origin.get("kind", "user") != "user":
                    continue
                key = data.get("id")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                text = clean_prompt(content_text(data.get("content")))
                if text:
                    prompts.append(text)
    if not header or not prompts:
        return []
    return [{"session_id": str(header.get("id") or path.parent.name), "cwd": str(header.get("cwd") or ""), "title": title,
             "prompts": prompts, "created_at": header.get("createdAt"), "updated_at": updated, "open_scope": "app"}]


def doubao_conversations(values: Iterable[Any]) -> list[dict[str, Any]]:
    """Merge cache snapshots by conversation and message revision, using current keys only."""
    conversations: dict[str, dict[str, Any]] = {}
    messages: dict[str, dict[str, dict[str, Any]]] = {}
    def walk(value: Any):
        if isinstance(value, Mapping):
            cid = str(value.get("conversation_id") or "")
            if cid and ("name" in value or "conversation_type" in value):
                previous = conversations.get(cid, {})
                if int(value.get("conv_version") or 0) >= int(previous.get("conv_version") or 0):
                    conversations[cid] = {**previous, **value}
            if cid and "message_id" in value:
                mid = str(value["message_id"])
                previous = messages.setdefault(cid, {}).get(mid, {})
                if int(value.get("message_body_version") or 0) >= int(previous.get("message_body_version") or 0):
                    messages[cid][mid] = dict(value)
            for nested in value.values():
                if isinstance(nested, (Mapping, list, tuple)):
                    walk(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                walk(nested)
    for value in values:
        walk(value)
    results = []
    for cid, conversation in conversations.items():
        rows = sorted(messages.get(cid, {}).values(), key=lambda r: int(r.get("index_in_conv") or 0))
        prompts = []
        for row in rows:
            # The observed cache uses 1 for a person and 2 for the assistant.
            if row.get("role") != "user" and str(row.get("user_type")) != "1":
                continue
            blocks = row.get("content_block") or json_value(row.get("content"), row.get("content", ""))
            text = clean_prompt(content_text(blocks))
            if text:
                prompts.append(text)
        # Built-in pinned bots and welcome messages are not user work.
        if not prompts:
            continue
        results.append({"session_id": cid, "title": str(conversation.get("name") or ""), "cwd": "", "prompts": prompts,
                        "created_at": conversation.get("create_time"), "updated_at": rows[-1].get("update_time") or rows[-1].get("create_time"),
                        "open_scope": "app", "coverage": "cached"})
    return results


def current_leveldb_records(database):
    """Ignore obsolete tables and honor tombstones before deserializing any value."""
    from vendor.chromium.storage_formats.ccl_leveldb import KeyState, ManifestFile
    current = (database.in_dir_path / "CURRENT").read_text().strip()
    if not re.fullmatch(r"MANIFEST-\d+", current):
        raise ValueError("invalid IndexedDB manifest")
    active_tables, log_number, previous_log = set(), 0, 0
    manifest = ManifestFile(database.in_dir_path / current)
    try:
        for edit in manifest:
            active_tables.difference_update(item.file_no for item in edit.deleted_files)
            active_tables.update(item.file_no for item in edit.new_files)
            if edit.log_number is not None:
                log_number = edit.log_number
            if edit.prev_log_number is not None:
                previous_log = edit.prev_log_number
    finally:
        manifest.close()
    latest = {}
    for row in database.iterate_records_raw():
        origin = Path(row.origin_file)
        number = int(origin.stem)
        if origin.suffix in {".ldb", ".sst"} and number not in active_tables:
            continue
        if origin.suffix == ".log" and number < log_number and number != previous_log:
            continue
        key = row.user_key
        if key not in latest or row.seq > latest[key].seq:
            latest[key] = row
    return [row for row in latest.values() if row.state == KeyState.Live]


def read_doubao(path: Path) -> list[dict[str, Any]]:
    from vendor.chromium import ccl_chromium_indexeddb as indexeddb

    class CurrentIndexedDb(indexeddb.IndexedDb):
        def _cache_records(self):
            # Upstream is a forensic reader, so it intentionally includes old and
            # deleted versions. Finder must keep only the latest live key.
            self._fetched_records = current_leveldb_records(self._db)

    db = CurrentIndexedDb(path, path.with_name(path.name.replace(".leveldb", ".blob")))
    values, failures = [], []
    try:
        for dbid in db.global_metadata.db_ids:
            if not dbid.name.startswith("DoubaoPC_"):
                continue
            store = indexeddb.WrappedDatabase(db, dbid)
            for name in ("conversations", "messages"):
                if name not in store.object_store_names:
                    continue
                values.extend(r.value for r in store[name].iterate_records(bad_deserializer_data_handler=lambda k, v: failures.append(True)))
        if failures:
            raise ValueError("Doubao conversation cache format unsupported; refresh after opening the task in Doubao Work")
        return doubao_conversations(values)
    finally:
        db.close()


READERS = {"workbuddy": read_workbuddy, "qwenwork": read_qwenwork, "doubao-work": read_doubao, "deepseek-harness": read_deepseek}
