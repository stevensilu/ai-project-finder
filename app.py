#!/usr/bin/env python3
"""AI Project Finder: local-only cross-AI session index and dashboard."""

from __future__ import annotations

import argparse
import errno
import gzip
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
from collections import Counter
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urlparse


# One place to state the release. README badges, the packaged archives, and the
# Server header all read from here, which is how server_version drifted to 1.0
# while the published releases were on 1.2.0.
APP_VERSION = "1.3.0"

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
INDEX_FILE = DATA_DIR / "index.json"
MANUAL_FILE = DATA_DIR / "manual.json"
OPEN_LOG_FILE = DATA_DIR / "open.log"
CONFIG_FILE = APP_DIR / "config.json"
DEMO_FIXTURE_FILE = APP_DIR / "demo" / "fixtures.json"
DEMO_DEFAULT_PORT = 4390
DEMO_MODE = False
IS_WINDOWS = os.name == "nt"
APP_LOCALE = "en"

# The default for max_prompt_chars: how long one request may be before it is
# shortened. It sits well above the longest request seen in real histories, so it
# never fires on ordinary work and only bounds a pathological paste. The old 9000
# default was spent across a whole session rather than per request, which is what
# put the later turns of a long conversation out of reach.
MAX_TURN_CHARS = 50_000

DEFAULT_CONFIG: dict[str, Any] = {
    "port": 4388,
    "max_prompt_chars": MAX_TURN_CHARS,
    "locale": "en",
    "sources": {
        "codex": "auto",
        "claude": "auto",
        "kimi": "auto",
        "kimi-desktop": "auto",
    },
}

HOME_DIR = Path.home()
HOME_PATTERN = re.escape(str(HOME_DIR))
PATH_SEPARATOR_PATTERN = r"[\\/]"
PATH_RE = re.compile(
    rf"{HOME_PATTERN}{PATH_SEPARATOR_PATTERN}[^\n\r\"'<>]{{1,360}}?\.(?:html?|xlsx?|csv|pptx?|pdf|docx?|md|txt|json|png|jpe?g|gif|mp4|mov|zip|skill)",
    re.IGNORECASE,
)
INTERNAL_STORAGE_FILE_RE = re.compile(
    rf"{HOME_PATTERN}/Library/Application Support/[^\n\r\"'<>]{{1,800}}?\.(?:html?|xlsx?|csv|pptx?|pdf|docx?|md|txt|json|png|jpe?g|gif|mp4|mov|zip|skill)",
    re.IGNORECASE,
)
INTERNAL_STORAGE_PATH_RE = re.compile(
    rf"{HOME_PATTERN}/Library/Application Support/[^\s\n\r\"'<>]+",
    re.IGNORECASE,
)
# Any location, not only Application Support: skills also ship from plugin
# caches under the home directory, and those payloads were reaching the index.
INJECTED_SKILL_PAYLOAD_RE = re.compile(
    rf"\s*Base directory for this skill:\s*{HOME_PATTERN}{PATH_SEPARATOR_PATTERN}[\s\S]*$",
    re.IGNORECASE,
)
SPACE_RE = re.compile(r"\s+")
DATE_PART_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# One unguessable token per launch. The dashboard page receives it as a strict
# same-site cookie, so a website cannot reach the local API even when it knows
# the port. Binding to 127.0.0.1 alone does not stop a browser on this computer
# from being used as the courier.
SESSION_TOKEN = secrets.token_urlsafe(24)
SESSION_COOKIE_NAME = "apf_session"
SAME_SITE_FETCH_VALUES = {"same-origin", "none"}
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)
NON_USER_CODEX_THREAD_SOURCES = {"subagent", "automation"}

# Folder conventions differ per person, so project naming reads them from
# config.json rather than carrying one contributor's layout in the source.
DEFAULT_NAMING: dict[str, list[str]] = {
    # A folder whose next segment names the project, e.g. .../projects/atlas.
    "project_markers": ["projects"],
    # A folder whose next segment names a client. A numbered prefix is allowed,
    # so a marker of "客户" also matches a folder named "1.1 客户".
    "client_markers": ["clients", "客户"],
    # A workspace that files work under a date folder, e.g. .../codex/2026-07-25/atlas.
    "dated_workspace_markers": ["codex"],
    # Folder names too generic to be a project name on their own. Keep this
    # list short: what follows is a guess from the request text, which is less
    # stable than a dull but consistent folder name.
    "ignore_dirs": [
        "documents",
        "downloads",
        "desktop",
        "tmp",
        "new-chat",
        "new-chat-2",
        "new-chat-3",
    ],
}
NAMING_RULES: dict[str, list[str]] = {
    key: list(value) for key, value in DEFAULT_NAMING.items()
}

# Reading the index from memory while the file is unchanged, and reusing parsed
# records for transcripts that have not changed, keeps both an open action and a
# refresh off the full-rescan path.
INDEX_CACHE: dict[str, Any] = {"key": None, "payload": None}
INDEX_CACHE_LOCK = threading.Lock()
# The compressed copy of the last body sent, so repeat reads skip the work.
GZIP_CACHE: dict[str, Any] = {"raw": None, "gzip": None}
GZIP_CACHE_LOCK = threading.Lock()
# Below this, a gzip frame costs more than it saves.
GZIP_MIN_BYTES = 1024
BUILD_LOCK = threading.Lock()
PARSE_CACHE_FILE = DATA_DIR / "parse-cache.json"
PROJECTS_FILE = DATA_DIR / "projects.json"
# Bump when a parser changes what it extracts, so stale entries are discarded.
PARSE_CACHE_VERSION = 3


def environment_home(name: str, default: Path) -> Path:
    raw = str(os.environ.get(name) or "").strip()
    return Path(raw).expanduser() if raw else default


def normalize_locale(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    return "zh-CN" if raw.startswith("zh") else "en"


def automatic_source_paths() -> dict[str, list[Path]]:
    codex_home = environment_home("CODEX_HOME", HOME_DIR / ".codex")
    claude_home = environment_home("CLAUDE_CONFIG_DIR", HOME_DIR / ".claude")
    kimi_home = environment_home("KIMI_CODE_HOME", HOME_DIR / ".kimi-code")
    paths = {
        "codex": [codex_home / "sessions"],
        "claude": [claude_home / "projects"],
        "kimi": [kimi_home / "sessions"],
        "kimi-desktop": [],
    }
    if IS_WINDOWS:
        for variable in ("APPDATA", "LOCALAPPDATA"):
            base = str(os.environ.get(variable) or "").strip()
            if not base:
                continue
            for app_folder in ("kimi-desktop", "Kimi Desktop", "Kimi"):
                paths["kimi-desktop"].append(
                    Path(base)
                    / app_folder
                    / "daimon-share"
                    / "daimon"
                    / "runtime"
                    / "kimi-code"
                    / "home"
                    / "sessions"
                )
        paths["kimi-desktop"] = list(dict.fromkeys(paths["kimi-desktop"]))
    elif Path("/Applications").exists():
        paths["kimi-desktop"].append(
            HOME_DIR
            / "Library"
            / "Application Support"
            / "kimi-desktop"
            / "daimon-share"
            / "daimon"
            / "runtime"
            / "kimi-code"
            / "home"
            / "sessions"
        )
    return paths


def expand_source_value(value: Any) -> list[Path]:
    values = value if isinstance(value, list) else [value]
    paths: list[Path] = []
    for item in values:
        text = os.path.expandvars(str(item or "").strip())
        if not text or text.lower() == "auto":
            continue
        paths.append(Path(text).expanduser())
    return paths


def resolved_source_paths(config: dict[str, Any]) -> dict[str, list[Path]]:
    automatic = automatic_source_paths()
    configured = config.get("sources", {})
    result: dict[str, list[Path]] = {}
    for source, defaults in automatic.items():
        value = configured.get(source, "auto") if isinstance(configured, dict) else "auto"
        custom = expand_source_value(value)
        result[source] = custom or defaults
    return result


def load_demo_payload() -> dict[str, Any]:
    """Load synthetic records without touching local AI history or data files."""
    try:
        fixture = json.loads(DEMO_FIXTURE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("demo fixture unavailable") from exc
    rows = fixture.get("records", []) if isinstance(fixture, dict) else []
    if not isinstance(rows, list):
        raise RuntimeError("demo fixture records must be a list")

    now = datetime.now(tz=timezone.utc)
    records: list[dict[str, Any]] = []
    locale_overrides = (
        fixture.get("translations", {}).get(APP_LOCALE, {})
        if isinstance(fixture.get("translations"), dict)
        else {}
    )
    labels = {
        "codex": "Codex",
        "claude": "Claude",
        "kimi": "Kimi Code",
        "kimi-desktop": "Kimi Desktop",
    }
    for position, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or "").strip().lower()
        session_id = str(raw.get("session_id") or f"demo-{position:03d}").strip()
        localized = (
            locale_overrides.get(session_id, {})
            if isinstance(locale_overrides, dict)
            else {}
        )
        if not isinstance(localized, dict):
            localized = {}
        project = str(
            localized.get("project") or raw.get("project") or "Demo Project"
        ).strip()
        title = str(localized.get("title") or raw.get("title") or project).strip()
        if source not in labels or not SESSION_ID_RE.fullmatch(session_id):
            raise RuntimeError(f"invalid demo record at position {position}")
        try:
            updated_hours = max(0.0, float(raw.get("updated_hours_ago", position)))
            created_hours = max(updated_hours, float(raw.get("created_hours_ago", updated_hours + 2)))
            message_count = max(1, int(raw.get("message_count", 1)))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid demo timing at position {position}") from exc
        updated_at = (now - timedelta(hours=updated_hours)).isoformat()
        created_at = (now - timedelta(hours=created_hours)).isoformat()
        default_workspace = (
            f"~/演示工作区/{project}"
            if APP_LOCALE == "zh-CN"
            else f"~/Demo Workspaces/{project}"
        )
        cwd = str(localized.get("cwd") or raw.get("cwd") or default_workspace).strip()
        excerpt = str(localized.get("excerpt") or raw.get("excerpt") or "").strip()
        raw_artifacts = localized.get("artifacts", raw.get("artifacts", []))
        artifacts = [
            str(item).strip()
            for item in raw_artifacts
            if str(item).strip()
        ] if isinstance(raw_artifacts, list) else []
        record = {
            "id": f"{source}:{session_id}",
            "source": source,
            "source_label": labels[source],
            "session_id": session_id,
            "session_path": f"demo://sessions/{session_id}",
            "cwd": cwd,
            "project": project,
            "customer": "",
            "title": title,
            "excerpt": excerpt,
            "artifacts": artifacts,
            "created_at": created_at,
            "updated_at": updated_at,
            "message_count": message_count,
            "origin": str(localized.get("origin") or raw.get("origin") or labels[source]),
        }
        if source == "kimi-desktop":
            record["open_label"] = "Open Kimi Desktop ↗"
        records.append(record)

    records.sort(key=lambda item: str(item["updated_at"]), reverse=True)
    counts = Counter(str(item["source"]) for item in records)
    projects = {str(item["project"]) for item in records if item.get("project")}
    return {
        "demo": True,
        "locale": APP_LOCALE,
        "generated_at": now.isoformat(),
        "records": records,
        "summary": {
            "records": len(records),
            "projects": len(projects),
            "sources": dict(counts),
            "errors": 0,
            "available_sources": len(counts),
        },
        "source_status": {
            source: {"available": True, "paths": [], "demo": True}
            for source in counts
        },
        "warnings": [],
    }


def demo_open_mode(record: dict[str, Any], action: str) -> str:
    """Return a display-only open mode and never invoke a system application."""
    if action == "workspace":
        return "workspace"
    source = str(record.get("source") or "").strip().lower()
    if action == "cli":
        if source != "kimi":
            raise ValueError("CLI open is only available for Kimi Code sessions")
        return "kimi-cli"
    if action != "session":
        raise ValueError("unsupported action")
    return {
        "codex": "codex",
        "claude": "claude",
        "kimi": "kimi-web",
        "kimi-desktop": "kimi-desktop",
    }.get(source, "local")


def compressed_index_body(body: bytes) -> bytes | None:
    """Compress a response body, reusing the last result for an identical body.

    Compressing the whole index costs tens of milliseconds, and the index is
    served again on every open action and health poll.
    """
    with GZIP_CACHE_LOCK:
        if GZIP_CACHE.get("raw") == body:
            cached = GZIP_CACHE.get("gzip")
            if isinstance(cached, bytes):
                return cached
    try:
        compressed = gzip.compress(body, 6)
    except (OSError, ValueError):
        return None
    with GZIP_CACHE_LOCK:
        GZIP_CACHE["raw"] = body
        GZIP_CACHE["gzip"] = compressed
    return compressed


def cache_index_payload(payload: dict[str, Any]) -> None:
    try:
        stat = INDEX_FILE.stat()
    except OSError:
        return
    with INDEX_CACHE_LOCK:
        INDEX_CACHE["key"] = (stat.st_mtime_ns, stat.st_size)
        INDEX_CACHE["payload"] = payload


def load_index_payload() -> dict[str, Any]:
    """Serve the index from memory while the file on disk is unchanged.

    Opening a result used to re-read and re-parse the whole index, which grows
    with the number of indexed sessions.
    """
    if DEMO_MODE:
        return load_demo_payload()
    try:
        stat = INDEX_FILE.stat()
    except OSError:
        return build_index()
    key = (stat.st_mtime_ns, stat.st_size)
    with INDEX_CACHE_LOCK:
        cached = INDEX_CACHE["payload"]
        if INDEX_CACHE["key"] == key and isinstance(cached, dict):
            return cached
    try:
        payload = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return build_index()
    if not isinstance(payload, dict):
        return build_index()
    with INDEX_CACHE_LOCK:
        INDEX_CACHE["key"] = key
        INDEX_CACHE["payload"] = payload
    return payload


def find_indexed_record(record_id: str) -> dict[str, Any] | None:
    if not record_id or len(record_id) > 260:
        return None
    records = load_index_payload().get("records", [])
    if not isinstance(records, list):
        return None
    return next(
        (record for record in records if isinstance(record, dict) and str(record.get("id") or "") == record_id),
        None,
    )


def system_open_target(target: str) -> None:
    """Hand a path or URL to the desktop with no shell in between.

    The Windows branch used to run `cmd.exe /c start "" <target>`. A folder or
    URL containing & or ^ would then be read as another command, so the value
    now goes to ShellExecute as a single argument.
    """
    if IS_WINDOWS:
        start_file = getattr(os, "startfile", None)
        if start_file is None:
            raise FileNotFoundError("system opener unavailable")
        start_file(target)
        return
    opener = "/usr/bin/open" if Path("/usr/bin/open").exists() else shutil.which("xdg-open")
    if not opener:
        raise FileNotFoundError("system opener unavailable")
    completed = subprocess.run(
        [str(opener), target],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=8,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        raise OSError(detail or f"system opener exited with {completed.returncode}")


def record_open_event(
    record: dict[str, Any],
    action: str,
    *,
    outcome: str,
    mode: str = "",
    error: str = "",
) -> None:
    """Keep a small local diagnostic trail without storing prompt content."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if OPEN_LOG_FILE.exists() and OPEN_LOG_FILE.stat().st_size > 1_000_000:
            OPEN_LOG_FILE.replace(OPEN_LOG_FILE.with_suffix(".previous.log"))
        event = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "record_id": str(record.get("id") or ""),
            "source": str(record.get("source") or ""),
            "action": action,
            "outcome": outcome,
            "mode": mode,
            "error": error[:300],
        }
        with OPEN_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def claude_desktop_session_roots() -> list[tuple[str, Path]]:
    """Return observed Claude Desktop profiles, newest activity first."""
    if IS_WINDOWS:
        application_roots = [
            Path(value)
            for value in (
                os.environ.get("APPDATA"),
                os.environ.get("LOCALAPPDATA"),
            )
            if value
        ]
        profile_roots = [(root, root) for root in dict.fromkeys(application_roots)]
    else:
        profile_roots = [
            (
                HOME_DIR / "Library" / "Application Support",
                HOME_DIR / "Library" / "Logs",
            )
        ]
    candidates: list[tuple[float, str, Path]] = []
    profile_names = ["Claude"]
    configured_profiles = [
        item.strip()
        for item in str(os.environ.get("AI_PROJECT_FINDER_CLAUDE_PROFILES") or "").split(",")
        if item.strip()
    ]
    profile_names.extend(configured_profiles)
    for application_support, logs_root in profile_roots:
        discovered_profiles = list(profile_names)
        try:
            discovered_profiles.extend(
                path.name
                for path in application_support.glob("Claude*")
                if (path / "claude-code-sessions").is_dir()
            )
        except OSError:
            pass
        for profile in dict.fromkeys(discovered_profiles):
            sessions_root = application_support / profile / "claude-code-sessions"
            if not sessions_root.is_dir():
                continue
            activity_times: list[float] = []
            try:
                activity_times.append(sessions_root.stat().st_mtime)
            except OSError:
                pass
            try:
                activity_times.extend(
                    path.stat().st_mtime
                    for path in (logs_root / profile).glob("main*.log")
                )
            except OSError:
                pass
            candidates.append((max(activity_times, default=0.0), profile, sessions_root))
    candidates.sort(key=lambda item: item[0], reverse=True)
    unique: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for _, profile, sessions_root in candidates:
        if sessions_root in seen:
            continue
        seen.add(sessions_root)
        unique.append((profile, sessions_root))
    return unique


def active_claude_desktop_sessions_root() -> tuple[str, Path] | None:
    """Return the most recently active Claude Desktop profile's metadata root."""
    roots = claude_desktop_session_roots()
    return roots[0] if roots else None


def load_claude_desktop_session_map(*, all_profiles: bool = False) -> dict[str, dict[str, str]]:
    """Map Claude Code CLI IDs to their original Desktop session metadata."""
    roots = claude_desktop_session_roots()
    if not roots:
        return {}
    ranked: dict[str, tuple[tuple[int, int, int, int], dict[str, str]]] = {}
    selected_roots = roots if all_profiles else roots[:1]
    for profile_priority, (profile, sessions_root) in enumerate(reversed(selected_roots), start=1):
        for path in sessions_root.rglob("local_*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            cli_session_id = str(payload.get("cliSessionId") or "").strip()
            desktop_session_id = str(payload.get("sessionId") or path.stem).strip()
            if not SESSION_ID_RE.fullmatch(cli_session_id) or not SESSION_ID_RE.fullmatch(desktop_session_id):
                continue
            title = str(payload.get("title") or "").strip()
            title_source = str(payload.get("titleSource") or "").strip().lower()
            try:
                last_activity = int(payload.get("lastActivityAt") or 0)
            except (TypeError, ValueError):
                last_activity = 0
            # Preserve original titles across profiles. For otherwise equal
            # records, prefer the profile with the most recent activity.
            rank = (
                int(bool(title)),
                int(title_source == "user"),
                profile_priority,
                last_activity,
            )
            metadata = {
                "desktop_session_id": desktop_session_id,
                "desktop_title": title,
                "desktop_title_source": title_source,
                "desktop_profile": profile,
            }
            current = ranked.get(cli_session_id)
            if current is None or rank > current[0]:
                ranked[cli_session_id] = (rank, metadata)
    return {cli_id: value[1] for cli_id, value in ranked.items()}


def kimi_binary() -> Path:
    kimi_home = environment_home("KIMI_CODE_HOME", HOME_DIR / ".kimi-code")
    executable_names = ("kimi.exe", "kimi") if IS_WINDOWS else ("kimi",)
    candidates = [
        directory / executable
        for directory in (kimi_home / "bin", HOME_DIR / ".local" / "bin")
        for executable in executable_names
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    discovered = shutil.which("kimi")
    if discovered:
        return Path(discovered)
    raise FileNotFoundError("Kimi Code unavailable")


def find_kimi_web_origin() -> str | None:
    kimi_home = environment_home("KIMI_CODE_HOME", HOME_DIR / ".kimi-code")
    instances_dir = kimi_home / "server" / "instances"
    try:
        instance_files = sorted(
            instances_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None

    for instance_file in instance_files:
        try:
            payload = json.loads(instance_file.read_text(encoding="utf-8"))
            host = str(payload.get("host") or "").strip().lower()
            port = int(payload.get("port") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if host not in LOOPBACK_HOSTS or not 1 <= port <= 65535:
            continue
        try:
            with socket.create_connection((host, port), timeout=0.25):
                display_host = f"[{host}]" if ":" in host else host
                return f"http://{display_host}:{port}"
        except OSError:
            continue
    return None


def ensure_kimi_web_origin() -> str:
    origin = find_kimi_web_origin()
    if origin:
        return origin

    subprocess.Popen(
        [str(kimi_binary()), "web", "--no-open"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    for _ in range(50):
        time.sleep(0.1)
        origin = find_kimi_web_origin()
        if origin:
            return origin
    raise FileNotFoundError("Kimi Web UI did not start")


def kimi_cli_launcher(session_id: str) -> Path:
    binary = kimi_binary()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        launcher = DATA_DIR / "open-kimi-session.cmd"
        escaped_binary = str(binary).replace('"', '""')
        escaped_session = session_id.replace('"', '""')
        command = (
            "@echo off\n"
            f'"{escaped_binary}" --session "{escaped_session}"\n'
        )
    else:
        launcher = DATA_DIR / "open-kimi-session.command"
        command = (
            "#!/bin/zsh\n"
            f"exec {shlex.quote(str(binary))} --session {shlex.quote(session_id)}\n"
        )
    launcher.write_text(command, encoding="utf-8")
    if not IS_WINDOWS:
        launcher.chmod(0o700)
    return launcher


def build_open_target(record: dict[str, Any], action: str) -> tuple[str, str]:
    if action == "workspace":
        raw_path = str(record.get("cwd") or "").strip()
        if not raw_path:
            raise ValueError("workspace unavailable")
        workspace = Path(raw_path).expanduser()
        if workspace.is_file():
            workspace = workspace.parent
        if not workspace.is_dir():
            raise FileNotFoundError("workspace unavailable")
        return str(workspace), "workspace"

    source = str(record.get("source") or "").lower()
    session_id = str(record.get("session_id") or "").strip()
    if source in {"codex", "claude", "kimi", "kimi-desktop"} and not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("invalid session id")
    if source in {"codex", "claude", "kimi", "kimi-desktop"}:
        session_path = Path(str(record.get("session_path") or "")).expanduser()
        if not session_path.is_file():
            raise FileNotFoundError("session transcript unavailable; refresh the index")
    encoded_id = quote(session_id, safe="")

    if action == "cli":
        if source != "kimi":
            raise ValueError("CLI open is only available for Kimi Code sessions")
        return str(kimi_cli_launcher(session_id)), "kimi-cli"
    if action != "session":
        raise ValueError("unsupported action")

    if source == "codex":
        return f"codex://threads/{encoded_id}", "codex"
    if source == "claude":
        # The active profile may have changed since the index was built.
        # Resolve it at click time so CC Switch and the official profile do not
        # leave stale Desktop IDs in the page.
        active_metadata = load_claude_desktop_session_map().get(session_id, {})
        desktop_session_id = str(active_metadata.get("desktop_session_id") or "").strip()
        if SESSION_ID_RE.fullmatch(desktop_session_id):
            encoded_desktop_id = quote(desktop_session_id, safe="")
            return (
                f"claude://claude.ai/claude-code-desktop/{encoded_desktop_id}",
                "claude-desktop",
            )
        return f"claude://resume?session={encoded_id}", "claude"
    if source == "kimi":
        origin = find_kimi_web_origin()
        if not origin:
            raise FileNotFoundError("Kimi Web UI unavailable")
        return f"{origin}/sessions/{encoded_id}", "kimi-web"
    if source == "kimi-desktop":
        return "kimi-work://home", "kimi-desktop"

    location = str(record.get("session_path") or record.get("cwd") or "").strip()
    parsed = urlparse(location)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return location, "web"
    local_target = Path(location).expanduser()
    if local_target.exists():
        return str(local_target), "local"
    raise FileNotFoundError("session target unavailable")


def launch_record(record: dict[str, Any], action: str) -> str:
    try:
        if action == "session" and str(record.get("source") or "").lower() == "kimi":
            ensure_kimi_web_origin()
        target, mode = build_open_target(record, action)
        system_open_target(target)
        record_open_event(record, action, outcome="opened", mode=mode)
        return mode
    except Exception as exc:
        record_open_event(
            record,
            action,
            outcome="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


def load_config() -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        try:
            custom = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            config.update({k: v for k, v in custom.items() if k != "sources"})
            config["sources"].update(custom.get("sources", {}))
        except (OSError, json.JSONDecodeError):
            pass
    return config


def read_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                kind = item.get("type", "")
                if kind in {"text", "input_text", "output_text"}:
                    value = item.get("text") or item.get("content")
                    if isinstance(value, str):
                        parts.append(value)
        return "\n".join(parts)
    if isinstance(content, dict):
        value = content.get("text") or content.get("content")
        return value if isinstance(value, str) else ""
    return ""


def clean_user_text(text: str) -> str:
    if not text:
        return ""
    block_names = (
        "recommended_plugins",
        "environment_context",
        "INSTRUCTIONS",
        "permissions instructions",
        "app-context",
    )
    # Claude Desktop may append an entire skill definition to a user prompt.
    # It is runtime context rather than a user-authored request.
    cleaned = INJECTED_SKILL_PAYLOAD_RE.sub("", text)
    # Desktop AI clients also inject private attachment-cache paths. Keep the
    # meaningful filename searchable, while dropping directories and UUIDs.
    cleaned = INTERNAL_STORAGE_FILE_RE.sub(
        lambda match: Path(match.group(0)).name,
        cleaned,
    )
    cleaned = INTERNAL_STORAGE_PATH_RE.sub(" ", cleaned)
    for name in block_names:
        cleaned = re.sub(
            rf"<{re.escape(name)}(?:\s[^>]*)?>[\s\S]*?</{re.escape(name)}>",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = re.sub(r"# AGENTS\.md instructions", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]{1,120}>", " ", cleaned)
    cleaned = SPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def title_from_prompts(prompts: list[str], fallback: str) -> str:
    for prompt in prompts:
        cleaned = clean_user_text(prompt)
        if len(cleaned) < 4:
            continue
        sentence = re.split(r"(?<=[。！？.!?])\s+|\n", cleaned, maxsplit=1)[0]
        return (sentence or cleaned)[:180].strip()
    return fallback


def iso_from_value(value: Any, fallback: float | None = None) -> str:
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            pass
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    seconds = fallback if fallback is not None else time.time()
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def resolve_naming_rules(config: dict[str, Any]) -> dict[str, list[str]]:
    configured = config.get("naming", {})
    if not isinstance(configured, dict):
        configured = {}
    rules: dict[str, list[str]] = {}
    for key, default in DEFAULT_NAMING.items():
        value = configured.get(key, default)
        values = value if isinstance(value, list) else [value]
        rules[key] = [str(item).strip() for item in values if str(item).strip()]
    return rules


def part_matches_marker(part: str, marker: str) -> bool:
    """Match a folder against a marker, allowing a numbered prefix."""
    lowered = str(part).strip().lower()
    target = str(marker).strip().lower()
    if not target:
        return False
    return lowered == target or lowered.endswith(f" {target}")


def marker_position(parts: list[str], markers: list[str], *, last: bool = False) -> int:
    positions = [
        index
        for index, part in enumerate(parts)
        if any(part_matches_marker(part, marker) for marker in markers)
    ]
    if not positions:
        return -1
    return positions[-1] if last else positions[0]


def is_ignored_dir(name: str) -> bool:
    lowered = str(name).strip().lower()
    if lowered == HOME_DIR.name.lower():
        return True
    return lowered in {item.lower() for item in NAMING_RULES.get("ignore_dirs", [])}


def looks_like_a_project_name(label: str) -> bool:
    """Reject a folder that is really a slugified sentence or a pasted link.

    Codex names a working folder after the opening request, which produces
    labels such as clone-https-github-com-someone-project. Those are unique per
    session, so accepting them fills the project list with one-off entries.
    """
    text = str(label).strip()
    if len(text) < 2 or len(text) > 48:
        return False
    lowered = text.lower()
    if "http" in lowered or "://" in lowered or lowered.startswith("www."):
        return False
    return text.count("-") < 4


def workspace_key(cwd: str) -> str:
    """A stable key for one working folder, so assignments survive small differences."""
    text = str(cwd or "").strip()
    if not text:
        return ""
    normalized = os.path.normpath(os.path.expanduser(text))
    return normalized.casefold() if IS_WINDOWS or Path("/Applications").exists() else normalized


def load_project_overrides() -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"by_workspace": {}, "by_record": {}}
    if not isinstance(payload, dict):
        return {"by_workspace": {}, "by_record": {}}
    return {
        "by_workspace": payload.get("by_workspace") if isinstance(payload.get("by_workspace"), dict) else {},
        "by_record": payload.get("by_record") if isinstance(payload.get("by_record"), dict) else {},
    }


def save_project_overrides(overrides: dict[str, dict[str, str]]) -> None:
    write_json_atomically(PROJECTS_FILE, overrides, prefix="projects-")


def assigned_project(record: dict[str, Any], overrides: dict[str, dict[str, str]]) -> str:
    by_record = overrides.get("by_record", {})
    named = str(by_record.get(str(record.get("id") or "")) or "").strip()
    if named:
        return named
    by_workspace = overrides.get("by_workspace", {})
    return str(by_workspace.get(workspace_key(str(record.get("cwd") or ""))) or "").strip()


def apply_project_overrides(
    records: list[dict[str, Any]], overrides: dict[str, dict[str, str]]
) -> None:
    """Let a saved assignment win over the derived label, on every build."""
    for record in records:
        derived = str(record.get("derived_project") or "")
        chosen = assigned_project(record, overrides)
        record["project"] = chosen or derived
        record["project_source"] = "assigned" if chosen else "derived"


def canonical_project(cwd: str, title: str) -> tuple[str, str]:
    normalized = cwd.rstrip("/")
    parts = [
        part
        for part in Path(normalized).parts
        if part not in {"/", "Users", "home", HOME_DIR.name}
    ]
    customer = ""

    project_at = marker_position(parts, NAMING_RULES.get("project_markers", []), last=True)
    if project_at >= 0 and project_at + 1 < len(parts):
        return parts[project_at + 1], customer

    client_at = marker_position(parts, NAMING_RULES.get("client_markers", []))
    if client_at >= 0 and client_at + 1 < len(parts):
        customer = parts[client_at + 1]
        tail = parts[client_at + 2 : client_at + 4]
        return " · ".join([customer, *tail]) if tail else customer, customer

    dated_at = marker_position(parts, NAMING_RULES.get("dated_workspace_markers", []))
    if dated_at >= 0:
        tail = parts[dated_at + 1 :]
        if tail and DATE_PART_RE.match(tail[0]):
            tail = tail[1:]
        if tail and not is_ignored_dir(tail[-1]) and looks_like_a_project_name(tail[-1]):
            return tail[-1], customer

    basename = Path(normalized).name if normalized else ""
    if basename and not is_ignored_dir(basename) and looks_like_a_project_name(basename):
        return basename, customer

    # Nothing in the path names the work. Summarising the opening request here
    # used to invent a project per session, which is what left the project view
    # full of one-off entries. An unnamed record stays unnamed, and can be
    # assigned to a project from the interface.
    return "", customer


def find_artifacts(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in PATH_RE.findall(text):
        path = match.rstrip(".,;:，。；：)]}、")
        if path not in seen:
            seen.add(path)
            found.append(path)
        if len(found) >= 10:
            break
    return found


def clip_prompts(prompts: list[str], max_prompt_chars: int) -> tuple[list[str], int]:
    """Shorten any single request past the ceiling, and say how many were cut."""
    clipped: list[str] = []
    truncated = 0
    for text in prompts:
        if len(text) > max_prompt_chars:
            text = text[:max_prompt_chars]
            truncated += 1
        clipped.append(text)
    return clipped, truncated


def make_record(
    *,
    source: str,
    session_id: str,
    session_path: str,
    cwd: str,
    title: str,
    prompts: list[str],
    created_at: str,
    updated_at: str,
    message_count: int,
    origin: str,
) -> dict[str, Any]:
    clean_prompts = [
        cleaned for cleaned in (clean_user_text(item) for item in prompts) if cleaned
    ]
    combined = "\n".join(clean_prompts)
    project, customer = canonical_project(cwd, title)
    artifacts = find_artifacts(combined)
    # The whole request history stays searchable. A second cap here used to keep
    # only the opening 9000 characters, which put the most recent request in a
    # long session out of reach and made max_prompt_chars inert above ~40000.
    excerpt = combined
    return {
        "id": f"{source}:{session_id}",
        "source": source,
        "source_label": {
            "codex": "Codex",
            "claude": "Claude",
            "kimi": "Kimi Code",
            "kimi-desktop": "Kimi Desktop",
        }.get(source, source),
        "session_id": session_id,
        "session_path": session_path,
        "cwd": cwd,
        "project": project,
        "derived_project": project,
        "customer": customer,
        "title": clean_user_text(title)[:220] or project,
        "excerpt": excerpt,
        "artifacts": artifacts,
        "created_at": created_at,
        "updated_at": updated_at,
        "message_count": message_count,
        "origin": origin,
    }


def is_user_initiated_codex_session(meta: dict[str, Any]) -> bool:
    thread_source = str(meta.get("thread_source") or "").strip().lower()
    if thread_source in NON_USER_CODEX_THREAD_SOURCES:
        return False
    source = meta.get("source")
    if isinstance(source, dict) and "subagent" in source:
        return False
    return True


def parse_codex(path: Path, max_prompt_chars: int) -> dict[str, Any] | None:
    meta: dict[str, Any] = {}
    prompts: list[str] = []
    truncated_turns = 0
    created = ""
    updated = ""
    message_count = 0
    for row in read_json_lines(path):
        timestamp = row.get("timestamp")
        if timestamp:
            created = created or str(timestamp)
            updated = str(timestamp)
        if row.get("type") == "session_meta":
            meta = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
            if meta and not is_user_initiated_codex_session(meta):
                return None
        if row.get("type") == "response_item":
            payload = row.get("payload", {})
            if isinstance(payload, dict) and payload.get("role") == "user":
                text = text_from_content(payload.get("content"))
                if text:
                    if len(text) > max_prompt_chars:
                        text = text[:max_prompt_chars]
                        truncated_turns += 1
                    prompts.append(text)
                    message_count += 1
    if not meta and not prompts:
        return None
    if meta and not is_user_initiated_codex_session(meta):
        return None
    stat = path.stat()
    session_id = str(meta.get("id") or meta.get("session_id") or path.stem)
    cwd = str(meta.get("cwd") or "")
    title = title_from_prompts(prompts, Path(cwd).name)
    record = make_record(
        source="codex",
        session_id=session_id,
        session_path=str(path),
        cwd=cwd,
        title=title,
        prompts=prompts,
        created_at=iso_from_value(created or meta.get("timestamp"), stat.st_ctime),
        updated_at=iso_from_value(updated, stat.st_mtime),
        message_count=message_count,
        origin=str(meta.get("source") or meta.get("originator") or "Codex session"),
    )
    record["truncated_turns"] = truncated_turns
    return record


def parse_claude(
    path: Path,
    max_prompt_chars: int,
    desktop_sessions: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    prompts: list[str] = []
    truncated_turns = 0
    created = ""
    updated = ""
    cwd = ""
    session_id = path.stem
    version = ""
    custom_title = ""
    ai_title = ""
    message_count = 0
    for row in read_json_lines(path):
        cwd = str(row.get("cwd") or cwd)
        session_id = str(row.get("sessionId") or session_id)
        version = str(row.get("version") or version)
        timestamp = row.get("timestamp")
        if timestamp:
            created = created or str(timestamp)
            updated = str(timestamp)
        # Subagent turns are agent instructions and tool results, not requests
        # the person typed. Current clients keep them in a separate subagents
        # folder that the indexer already skips; this check keeps them out even
        # when a client writes them into the main transcript instead.
        if row.get("type") == "user" and not row.get("isSidechain"):
            message = row.get("message", {})
            text = text_from_content(message.get("content") if isinstance(message, dict) else message)
            if text:
                if len(text) > max_prompt_chars:
                    text = text[:max_prompt_chars]
                    truncated_turns += 1
                prompts.append(text)
                message_count += 1
        if row.get("type") == "custom-title":
            candidate_title = str(row.get("customTitle") or "").strip()
            if candidate_title:
                custom_title = candidate_title
        if row.get("type") == "ai-title":
            candidate_ai_title = str(row.get("aiTitle") or "").strip()
            if candidate_ai_title:
                ai_title = candidate_ai_title
    if not prompts and not cwd:
        return None
    stat = path.stat()
    desktop_metadata = (desktop_sessions or {}).get(session_id, {})
    desktop_title = str(desktop_metadata.get("desktop_title") or "").strip()
    desktop_title_is_user_set = (
        str(desktop_metadata.get("desktop_title_source") or "").strip().lower() == "user"
    )
    # A title the person wrote outranks a generated one, and any client-side
    # title outranks a sentence sliced out of the first prompt.
    if custom_title:
        title, title_source = custom_title, "custom-title"
    elif desktop_title and desktop_title_is_user_set:
        title, title_source = desktop_title, "desktop-user"
    elif ai_title:
        title, title_source = ai_title, "ai-title"
    elif desktop_title:
        title, title_source = desktop_title, "desktop"
    else:
        title = title_from_prompts(prompts, Path(cwd).name)
        title_source = "first-prompt"
    record = make_record(
        source="claude",
        session_id=session_id,
        session_path=str(path),
        cwd=cwd,
        title=title,
        prompts=prompts,
        created_at=iso_from_value(created, stat.st_ctime),
        updated_at=iso_from_value(updated, stat.st_mtime),
        message_count=message_count,
        origin=f"Claude Code {version}".strip(),
    )
    desktop_session_id = str(desktop_metadata.get("desktop_session_id") or "").strip()
    if SESSION_ID_RE.fullmatch(desktop_session_id):
        record["desktop_session_id"] = desktop_session_id
        record["desktop_profile"] = str(desktop_metadata.get("desktop_profile") or "")
    record["title_source"] = title_source
    record["truncated_turns"] = truncated_turns
    return record


def parse_kimi(path: Path, max_prompt_chars: int) -> dict[str, Any] | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    title = str(state.get("title") or state.get("lastPrompt") or "")
    last_prompt = str(state.get("lastPrompt") or "")
    prompts = [title]
    if last_prompt and last_prompt != title:
        prompts.append(last_prompt)
    prompts, truncated_turns = clip_prompts(prompts, max_prompt_chars)
    cwd = str(state.get("cwd") or state.get("workDir") or "")
    session_id = str(state.get("id") or path.parent.name)
    stat = path.stat()
    record = make_record(
        source="kimi",
        session_id=session_id,
        session_path=str(path),
        cwd=cwd,
        title=title,
        prompts=prompts,
        created_at=iso_from_value(state.get("createdAt"), stat.st_ctime),
        updated_at=iso_from_value(state.get("updatedAt"), stat.st_mtime),
        message_count=max(1, len(prompts)),
        origin="Kimi Code",
    )
    record["truncated_turns"] = truncated_turns
    return record


def parse_kimi_desktop(path: Path, max_prompt_chars: int) -> dict[str, Any] | None:
    session_folder = path.parent.name
    if not session_folder.startswith(("conv-", "session_")):
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    title = str(state.get("title") or state.get("lastPrompt") or "")
    last_prompt = str(state.get("lastPrompt") or "")
    prompts = [title]
    if last_prompt and last_prompt != title:
        prompts.append(last_prompt)
    prompts, truncated_turns = clip_prompts(prompts, max_prompt_chars)
    cwd = str(state.get("cwd") or state.get("workDir") or "")
    session_id = str(state.get("id") or session_folder)
    stat = path.stat()
    record = make_record(
        source="kimi-desktop",
        session_id=session_id,
        session_path=str(path),
        cwd=cwd,
        title=title,
        prompts=prompts,
        created_at=iso_from_value(state.get("createdAt"), stat.st_ctime),
        updated_at=iso_from_value(state.get("updatedAt"), stat.st_mtime),
        message_count=max(1, len(prompts)),
        origin="Kimi Desktop / Work",
    )
    record["truncated_turns"] = truncated_turns
    record["open_label"] = "Open Kimi Desktop ↗"
    return record


def write_json_atomically(target: Path, payload: Any, *, prefix: str) -> None:
    """Write through a unique temporary file so two writers cannot interleave."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=prefix, suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        os.replace(temporary_name, target)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def load_manual() -> list[dict[str, Any]]:
    if not MANUAL_FILE.exists():
        return []
    try:
        rows = json.loads(MANUAL_FILE.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def load_parse_cache(max_prompt_chars: int) -> dict[str, dict[str, Any]]:
    """Return previously parsed records, or nothing when the cache cannot apply."""
    try:
        payload = json.loads(PARSE_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("version") != PARSE_CACHE_VERSION:
        return {}
    if int(payload.get("max_prompt_chars") or 0) != max_prompt_chars:
        return {}
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def store_parse_cache(
    entries: dict[str, dict[str, Any]], max_prompt_chars: int
) -> None:
    payload = {
        "version": PARSE_CACHE_VERSION,
        "max_prompt_chars": max_prompt_chars,
        "entries": entries,
    }
    try:
        write_json_atomically(PARSE_CACHE_FILE, payload, prefix="parse-cache-")
    except OSError:
        pass


def desktop_metadata_for(
    record: dict[str, Any] | None, desktop_sessions: dict[str, dict[str, str]]
) -> dict[str, str] | None:
    """Claude records depend on Desktop metadata, which lives outside the file."""
    if not record:
        return None
    return desktop_sessions.get(str(record.get("session_id") or "")) or None


def build_index() -> dict[str, Any]:
    if DEMO_MODE:
        return load_demo_payload()
    # One writer at a time: the startup refresh and a Refresh click can overlap.
    with BUILD_LOCK:
        return build_index_now()


def build_index_now() -> dict[str, Any]:
    config = load_config()
    global NAMING_RULES
    NAMING_RULES = resolve_naming_rules(config)
    max_chars = int(config.get("max_prompt_chars", MAX_TURN_CHARS))
    source_paths = resolved_source_paths(config)
    claude_desktop_sessions = load_claude_desktop_session_map(all_profiles=True)
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    notices: list[str] = []
    source_status: dict[str, dict[str, Any]] = {}
    previous_cache = load_parse_cache(max_chars)
    fresh_cache: dict[str, dict[str, Any]] = {}
    reused = 0

    adapters = {
        "codex": ("**/*.jsonl", parse_codex),
        "claude": (
            "**/*.jsonl",
            lambda path, limit: parse_claude(path, limit, claude_desktop_sessions),
        ),
        "kimi": ("**/state.json", parse_kimi),
        "kimi-desktop": ("**/state.json", parse_kimi_desktop),
    }
    for source, (pattern, parser) in adapters.items():
        roots = [root for root in source_paths.get(source, []) if root.exists()]
        source_status[source] = {
            "available": bool(roots),
            "paths": [str(root) for root in roots],
        }
        if not roots:
            notices.append(f"{source}: not detected on this computer")
            continue
        for root in roots:
            for path in root.glob(pattern):
                if source == "claude" and "subagents" in path.parts:
                    continue
                cache_key = str(path)
                try:
                    stat = path.stat()
                    fingerprint = [stat.st_mtime_ns, stat.st_size]
                    cached = previous_cache.get(cache_key)
                    if (
                        isinstance(cached, dict)
                        and cached.get("stat") == fingerprint
                        and cached.get("desktop")
                        == desktop_metadata_for(cached.get("record"), claude_desktop_sessions)
                    ):
                        record = cached.get("record")
                        reused += 1
                    else:
                        record = parser(path, max_chars)
                    fresh_cache[cache_key] = {
                        "stat": fingerprint,
                        "record": record,
                        "desktop": desktop_metadata_for(record, claude_desktop_sessions),
                    }
                    if record:
                        records.append(record)
                        # Say when a turn was clipped. Silent truncation is what
                        # made the missing text so hard to notice.
                        clipped = int(record.get("truncated_turns") or 0)
                        if clipped:
                            notices.append(
                                f"{source}: {path.name}: {clipped} turn(s) truncated "
                                f"at {MAX_TURN_CHARS} characters"
                            )
                except Exception as exc:  # one damaged session must not block the index
                    errors.append(f"{source}: {path.name}: {type(exc).__name__}")
    store_parse_cache(fresh_cache, max_chars)

    manual_rows = load_manual()
    for row in manual_rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or "other").lower()
        when = iso_from_value(row.get("updated_at"))
        title = str(row.get("title") or "")
        cwd = str(row.get("location") or "")
        record = make_record(
            source=source,
            session_id=str(row.get("id") or uuid.uuid4()),
            session_path=cwd,
            cwd=cwd,
            title=title,
            prompts=[str(row.get("notes") or "")],
            created_at=when,
            updated_at=when,
            message_count=1,
            origin="",
        )
        if row.get("project"):
            record["project"] = str(row["project"])
            record["derived_project"] = str(row["project"])
        # Marked so the interface can offer edit and delete, and can label the
        # origin in the reader's own language.
        record["manual"] = True
        record["manual_id"] = str(row.get("id") or "")
        record["notes"] = str(row.get("notes") or "")
        record["manual_source"] = str(row.get("source") or "")
        records.append(record)

    apply_project_overrides(records, load_project_overrides())

    deduplicated: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = str(record.get("id") or "")
        existing = deduplicated.get(record_id)
        if existing is None or (
            bool(Path(str(record.get("session_path") or "")).exists()),
            int(record.get("message_count") or 0),
            str(record.get("updated_at") or ""),
        ) > (
            bool(Path(str(existing.get("session_path") or "")).exists()),
            int(existing.get("message_count") or 0),
            str(existing.get("updated_at") or ""),
        ):
            deduplicated[record_id] = record
    records = sorted(
        deduplicated.values(),
        key=lambda item: item.get("updated_at", ""),
        reverse=True,
    )
    counts = Counter(item["source"] for item in records)
    projects = {item["project"] for item in records if item.get("project")}
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "records": records,
        "summary": {
            "records": len(records),
            "projects": len(projects),
            "sources": dict(counts),
            "errors": len(errors),
            "available_sources": sum(bool(item["available"]) for item in source_status.values()),
        },
        "source_status": source_status,
        "warnings": [*errors, *notices][:30],
        "reused_records": reused,
    }
    write_json_atomically(INDEX_FILE, payload, prefix="index-")
    cache_index_payload(payload)
    return payload


class FinderHandler(SimpleHTTPRequestHandler):
    server_version = f"AIProjectFinder/{APP_VERSION}"
    sys_version = ""
    issue_session_cookie = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if self.issue_session_cookie:
            self.issue_session_cookie = False
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}; Path=/; SameSite=Strict; HttpOnly",
            )
        super().end_headers()

    def host_header_is_loopback(self) -> bool:
        """Reject a Host that is not this local service.

        A website can point its own hostname at 127.0.0.1 (DNS rebinding) and
        then read the local API as same-origin. The browser still sends the
        website's hostname here, so the Host header is what separates the two.
        """
        raw = str(self.headers.get("Host") or "").strip()
        if not raw:
            return False
        if raw.startswith("["):
            host, _, remainder = raw[1:].partition("]")
            port = remainder[1:] if remainder.startswith(":") else ""
        else:
            host, _, port = raw.partition(":")
        if host.strip().lower() not in LOOPBACK_HOSTS:
            return False
        return not port or port == str(self.server.server_port)

    def request_token(self) -> str:
        header_token = str(self.headers.get("X-APF-Token") or "").strip()
        if header_token:
            return header_token
        jar = SimpleCookie()
        try:
            jar.load(str(self.headers.get("Cookie") or ""))
        except CookieError:
            return ""
        morsel = jar.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else ""

    def request_is_authorized(self) -> bool:
        return secrets.compare_digest(self.request_token(), SESSION_TOKEN)

    def request_is_same_origin(self) -> bool:
        """Reject an API call that another website asked the browser to send."""
        fetch_site = str(self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if fetch_site and fetch_site not in SAME_SITE_FETCH_VALUES:
            return False
        origin = str(self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        parsed = urlparse(origin)
        if parsed.scheme != "http":
            return False
        try:
            port = parsed.port
        except ValueError:
            return False
        return str(parsed.hostname or "").lower() in LOOPBACK_HOSTS and (
            port is None or port == self.server.server_port
        )

    def request_body_is_json(self) -> bool:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return False
        if length <= 0:
            return True
        media_type = str(self.headers.get("Content-Type") or "").split(";", 1)[0]
        return media_type.strip().lower() == "application/json"

    def deny(self, status: HTTPStatus, code: str, message: str) -> None:
        if self.command == "HEAD":
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_json({"ok": False, "error": message, "error_code": code}, status)

    def api_request_is_allowed(self) -> bool:
        """Run every local-boundary check and answer the caller when one fails."""
        if not self.request_is_same_origin():
            self.deny(
                HTTPStatus.FORBIDDEN,
                "cross_origin",
                "cross-origin request rejected",
            )
            return False
        if not self.request_is_authorized():
            self.deny(
                HTTPStatus.FORBIDDEN,
                "unauthorized",
                "local session token required; reload the dashboard",
            )
            return False
        if not self.request_body_is_json():
            self.deny(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "unsupported_media_type",
                "application/json body required",
            )
            return False
        return True

    def client_accepts_gzip(self) -> bool:
        encodings = str(self.headers.get("Accept-Encoding") or "").lower()
        return any(token.strip().split(";")[0] == "gzip" for token in encodings.split(","))

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        encoding = ""
        # The index carries every request a person typed, so it compresses well.
        # Small replies are left alone: framing one costs more than it saves.
        if len(body) >= GZIP_MIN_BYTES and self.client_accepts_gzip():
            compressed = compressed_index_body(body)
            if compressed is not None and len(compressed) < len(body):
                body = compressed
                encoding = "gzip"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        # No CORS headers: a cross-origin preflight must fail rather than pass.
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "GET, HEAD, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:
        self.issue_session_cookie = False
        if not self.host_header_is_loopback():
            self.deny(HTTPStatus.FORBIDDEN, "invalid_host", "unexpected Host header")
            return
        if urlparse(self.path).path.startswith("/api/"):
            self.deny(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "use GET")
            return
        self.issue_session_cookie = True
        super().do_HEAD()

    def do_GET(self) -> None:
        self.issue_session_cookie = False
        if not self.host_header_is_loopback():
            self.deny(HTTPStatus.FORBIDDEN, "invalid_host", "unexpected Host header")
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self.api_request_is_allowed():
                return
            if parsed.path == "/api/index":
                self.send_json(load_index_payload())
                return
            if parsed.path == "/api/health":
                # generated_at lets an open page notice that the refresh started
                # at launch has finished, without polling the whole index.
                payload = load_index_payload()
                self.send_json({
                    "ok": True,
                    "demo": DEMO_MODE,
                    "index_exists": True if DEMO_MODE else INDEX_FILE.exists(),
                    "generated_at": str(payload.get("generated_at") or ""),
                })
                return
            self.deny(HTTPStatus.NOT_FOUND, "unknown_endpoint", "unknown endpoint")
            return
        # Any page load from this computer receives the current session token,
        # so a bookmarked dashboard URL keeps working across restarts.
        self.issue_session_cookie = True
        if parsed.path == "/":
            query = parse_qs(parsed.query)
            if "lang" not in query:
                separator = "&" if parsed.query else ""
                self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
                self.send_header(
                    "Location",
                    f"/?{parsed.query}{separator}lang={quote(APP_LOCALE, safe='-')}",
                )
                self.end_headers()
                return
            self.path = "/index.html"
        super().do_GET()

    def read_body_json(self) -> dict[str, Any]:
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 100_000)
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_POST(self) -> None:
        self.issue_session_cookie = False
        if not self.host_header_is_loopback():
            self.deny(HTTPStatus.FORBIDDEN, "invalid_host", "unexpected Host header")
            return
        if not self.api_request_is_allowed():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/reindex":
            try:
                payload = load_demo_payload() if DEMO_MODE else build_index()
                self.send_json({
                    "ok": True,
                    "demo": DEMO_MODE,
                    "summary": payload["summary"],
                    "generated_at": payload["generated_at"],
                })
            except Exception as exc:
                self.send_json({"ok": False, "error": type(exc).__name__}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/open":
            request = self.read_body_json()
            record = find_indexed_record(str(request.get("record_id") or ""))
            action = str(request.get("action") or "session")
            if record is None:
                self.send_json({"ok": False, "error": "record not found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                mode = demo_open_mode(record, action) if DEMO_MODE else launch_record(record, action)
                self.send_json({"ok": True, "demo": DEMO_MODE, "mode": mode})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except FileNotFoundError as exc:
                self.send_json(
                    {"ok": False, "error": str(exc), "error_code": "target_unavailable"},
                    HTTPStatus.NOT_FOUND,
                )
            except subprocess.TimeoutExpired:
                self.send_json(
                    {"ok": False, "error": "system opener timed out", "error_code": "open_timeout"},
                    HTTPStatus.GATEWAY_TIMEOUT,
                )
            except OSError as exc:
                self.send_json(
                    {"ok": False, "error": str(exc) or type(exc).__name__, "error_code": "client_open_failed"},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return
        if parsed.path in {"/api/project/assign", "/api/project/rename"}:
            if DEMO_MODE:
                self.send_json(
                    {
                        "ok": False,
                        "error": "demo mode is read only",
                        "error_code": "demo_read_only",
                    },
                    HTTPStatus.FORBIDDEN,
                )
                return
            row = self.read_body_json()
            if parsed.path == "/api/project/assign":
                self.assign_project(row)
            else:
                self.rename_project(row)
            return
        if parsed.path in {"/api/manual", "/api/manual/update", "/api/manual/delete"}:
            if DEMO_MODE:
                self.send_json(
                    {
                        "ok": False,
                        "error": "demo mode is read only",
                        "error_code": "demo_read_only",
                    },
                    HTTPStatus.FORBIDDEN,
                )
                return
            row = self.read_body_json()
            if parsed.path == "/api/manual":
                self.create_manual_trace(row)
            elif parsed.path == "/api/manual/update":
                self.update_manual_trace(row)
            else:
                self.delete_manual_trace(row)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def assign_project(self, row: dict[str, Any]) -> None:
        """Attach one session, or a whole working folder, to a named project."""
        record = find_indexed_record(str(row.get("record_id") or ""))
        if record is None:
            self.send_json(
                {"ok": False, "error": "record not found", "error_code": "record_missing"},
                HTTPStatus.NOT_FOUND,
            )
            return
        name = str(row.get("project") or "").strip()[:120]
        scope = "workspace" if str(row.get("scope") or "") == "workspace" else "record"
        folder = workspace_key(str(record.get("cwd") or ""))
        if scope == "workspace" and not folder:
            scope = "record"
        overrides = load_project_overrides()
        bucket = "by_workspace" if scope == "workspace" else "by_record"
        key = folder if scope == "workspace" else str(record.get("id") or "")
        if name:
            overrides[bucket][key] = name
        else:
            # An empty name clears the assignment at both levels, so a record
            # cannot stay pinned by a folder rule the reader just cleared.
            overrides["by_record"].pop(str(record.get("id") or ""), None)
            if folder:
                overrides["by_workspace"].pop(folder, None)
        save_project_overrides(overrides)
        payload = build_index()
        self.send_json({"ok": True, "project": name, "scope": scope, "summary": payload["summary"]})

    def rename_project(self, row: dict[str, Any]) -> None:
        previous = str(row.get("from") or "").strip()
        name = str(row.get("to") or "").strip()[:120]
        if not previous or not name:
            self.send_json(
                {"ok": False, "error": "both names are required"}, HTTPStatus.BAD_REQUEST
            )
            return
        records = [
            record
            for record in load_index_payload().get("records", [])
            if isinstance(record, dict) and str(record.get("project") or "") == previous
        ]
        if not records:
            self.send_json(
                {"ok": False, "error": "project not found", "error_code": "project_missing"},
                HTTPStatus.NOT_FOUND,
            )
            return
        overrides = load_project_overrides()
        for record in records:
            folder = workspace_key(str(record.get("cwd") or ""))
            # Pinning the folder means later sessions in it inherit the name.
            if folder:
                overrides["by_workspace"][folder] = name
            else:
                overrides["by_record"][str(record.get("id") or "")] = name
        save_project_overrides(overrides)
        payload = build_index()
        self.send_json({"ok": True, "project": name, "records": len(records), "summary": payload["summary"]})

    def save_manual_traces(self, rows: list[dict[str, Any]]) -> None:
        write_json_atomically(MANUAL_FILE, rows, prefix="manual-")

    def create_manual_trace(self, row: dict[str, Any]) -> None:
        if not str(row.get("title") or "").strip():
            self.send_json({"ok": False, "error": "title is required"}, HTTPStatus.BAD_REQUEST)
            return
        manual = load_manual()
        row["id"] = str(uuid.uuid4())
        row["updated_at"] = row.get("updated_at") or datetime.now(tz=timezone.utc).isoformat()
        manual.append(row)
        self.save_manual_traces(manual)
        payload = build_index()
        self.send_json({"ok": True, "summary": payload["summary"]}, HTTPStatus.CREATED)

    def update_manual_trace(self, row: dict[str, Any]) -> None:
        trace_id = str(row.get("id") or "").strip()
        if not str(row.get("title") or "").strip():
            self.send_json({"ok": False, "error": "title is required"}, HTTPStatus.BAD_REQUEST)
            return
        manual = load_manual()
        position = next(
            (
                index
                for index, item in enumerate(manual)
                if isinstance(item, dict) and str(item.get("id") or "") == trace_id
            ),
            -1,
        )
        if not trace_id or position < 0:
            self.send_json(
                {"ok": False, "error": "trace not found", "error_code": "trace_missing"},
                HTTPStatus.NOT_FOUND,
            )
            return
        for field in ("source", "project", "title", "location", "notes"):
            if field in row:
                manual[position][field] = str(row.get(field) or "")
        manual[position]["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
        self.save_manual_traces(manual)
        payload = build_index()
        self.send_json({"ok": True, "summary": payload["summary"]})

    def delete_manual_trace(self, row: dict[str, Any]) -> None:
        trace_id = str(row.get("id") or "").strip()
        manual = load_manual()
        remaining = [
            item
            for item in manual
            if not (isinstance(item, dict) and str(item.get("id") or "") == trace_id)
        ]
        if not trace_id or len(remaining) == len(manual):
            self.send_json(
                {"ok": False, "error": "trace not found", "error_code": "trace_missing"},
                HTTPStatus.NOT_FOUND,
            )
            return
        self.save_manual_traces(remaining)
        payload = build_index()
        self.send_json({"ok": True, "summary": payload["summary"]})


def main() -> None:
    global APP_LOCALE, DEMO_MODE
    parser = argparse.ArgumentParser(description="AI Project Finder local dashboard")
    parser.add_argument("--build-only", action="store_true", help="refresh index and exit")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--open", action="store_true", help="open the dashboard in the default browser")
    parser.add_argument(
        "--locale",
        choices=("en", "zh-CN"),
        default=None,
        help="interface language; overrides config.json and AI_PROJECT_FINDER_LOCALE",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run with synthetic demo data on port 4390; no local sources are read",
    )
    args = parser.parse_args()
    DEMO_MODE = bool(args.demo)
    locale_override = args.locale or os.environ.get("AI_PROJECT_FINDER_LOCALE")
    config = {} if DEMO_MODE else load_config()
    APP_LOCALE = normalize_locale(
        locale_override or ("en" if DEMO_MODE else config.get("locale", "en"))
    )
    is_chinese = APP_LOCALE == "zh-CN"

    if args.build_only:
        payload = load_demo_payload() if DEMO_MODE else build_index()
        if is_chinese:
            print(
                f"已索引 {payload['summary']['records']} 个会话，"
                f"包含 {payload['summary']['projects']} 个项目线索。"
            )
        else:
            print(
                f"Indexed {payload['summary']['records']} sessions across "
                f"{payload['summary']['projects']} project clues."
            )
        return

    if DEMO_MODE:
        payload = load_demo_payload()
        if is_chinese:
            print(
                f"已载入 {payload['summary']['records']} 个合成会话，"
                f"包含 {payload['summary']['projects']} 个演示项目。"
            )
        else:
            print(
                f"Loaded {payload['summary']['records']} synthetic sessions across "
                f"{payload['summary']['projects']} demo projects."
            )
    elif not INDEX_FILE.exists():
        payload = build_index()
        if is_chinese:
            print(
                f"已索引 {payload['summary']['records']} 个会话，"
                f"包含 {payload['summary']['projects']} 个项目线索。"
            )
        else:
            print(
                f"Indexed {payload['summary']['records']} sessions across "
                f"{payload['summary']['projects']} project clues."
            )
    else:
        threading.Thread(target=build_index, daemon=True, name="index-refresh").start()

    port = args.port or (DEMO_DEFAULT_PORT if DEMO_MODE else int(config.get("port", 4388)))
    url = f"http://127.0.0.1:{port}/?lang={quote(APP_LOCALE, safe='-')}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), FinderHandler)
    except OSError as exc:
        if exc.errno in {errno.EADDRINUSE, 48, 98}:
            if is_chinese:
                print(f"AI Project Finder 已经在此地址运行：{url}")
            else:
                print(f"AI Project Finder is already available at {url}")
            if args.open:
                webbrowser.open(url)
            return
        raise
    if is_chinese:
        print(f"AI Project Finder 已在此地址运行：{url}")
    else:
        print(f"AI Project Finder is available at {url}")
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
