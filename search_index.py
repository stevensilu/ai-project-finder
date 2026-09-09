"""Local substring search with bounded list responses and paged source text.

The saved index remains complete within each adapter's per-turn limit. Only
result pages cross the HTTP boundary; no database or tokenizer dependency is
needed for Chinese, paths, partial filenames, and normalized Unicode phrases.
"""
from __future__ import annotations

import math
import re
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from datetime import datetime


PAGE_SIZE = 30
MAX_PAGE_SIZE = 60
TEXT_PAGE_SIZE = 12_000
SNIPPET_SIZE = 820
PROJECT_PREVIEW = 5


def normalize(value) -> str:
    return unicodedata.normalize("NFKC", str(value or "").lower())


def parse_query(raw: str) -> tuple[list[str], list[str]]:
    terms, sources = [], []
    for match in re.finditer(r'"([^"]*)"|(\S+)', raw):
        quoted, word = match.groups()
        if quoted is not None:
            if normalize(quoted):
                terms.append(normalize(quoted))
        elif word.lower().startswith("source:") and len(word) > 7:
            sources.append(normalize(word[7:]))
        else:
            terms.append(normalize(word))
    return terms, sources


def timestamp(value) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return 0


def first_match(raw: str, terms: list[str]) -> int:
    """Map an NFKC match back to raw text, including ligatures and combining marks."""
    if not terms:
        return -1
    window = 8192
    step = max(1, window - max(map(len, terms)))
    for start in range(0, len(raw), step):
        chunk = raw[start:start + window]
        folded = normalize(chunk)
        positions = [folded.find(term) for term in terms]
        at = min((p for p in positions if p >= 0), default=-1)
        if at < 0:
            continue
        if len(chunk) == len(folded):
            return start + at
        # The first prefix whose folded length passes the offset contains the
        # matching character, even when one character expands to several.
        low, high = 0, len(chunk)
        while low < high:
            middle = (low + high) // 2
            if len(normalize(chunk[:middle])) <= at:
                low = middle + 1
            else:
                high = middle
        return start + max(0, low - 1)
    return -1


def snippet(raw: str, terms: list[str]) -> tuple[str, int]:
    at = first_match(raw, terms)
    start = max(0, at - 120)
    end = min(len(raw), start + SNIPPET_SIZE)
    return ("…" if start else "") + raw[start:end] + ("…" if end < len(raw) else ""), start


def integer(params: dict, name: str, default: int, maximum: int) -> int:
    try:
        value = int(params.get(name, str(default)))
    except (ValueError, TypeError):
        raise ValueError(f"invalid {name}") from None
    if value < (1 if name == "limit" else 0) or value > maximum:
        raise ValueError(f"invalid {name}")
    return value


class SearchIndex:
    """One immutable index generation, with a small cache of ordered matches."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.records = payload.get("records", [])
        self.by_id = {r["id"]: r for r in self.records}
        self.revision = uuid.uuid4().hex
        self.now = time.time()
        self._folded = None
        self._matches = OrderedDict()
        self._lock = threading.Lock()

    def metadata(self) -> dict:
        return {
            **{key: value for key, value in self.payload.items() if key != "records"},
            "revision": self.revision,
            "projects": sorted({r.get("project") for r in self.records if r.get("project")}),
        }

    def _prepare(self):
        if self._folded is None:
            self._folded = {
                r["id"]: normalize(" ".join(str(v) for v in [
                    r.get("title"), r.get("project"), r.get("customer"), r.get("cwd"),
                    r.get("excerpt"), r.get("notes"), *(r.get("artifacts") or [])
                ] if v)) for r in self.records
            }

    def _score(self, record, terms):
        text = self._folded[record["id"]]
        title, project, customer, cwd = [normalize(record.get(k)) for k in ("title", "project", "customer", "cwd")]
        score = 0
        for term in terms:
            at, count = text.find(term), 0
            while at >= 0 and count < 64:
                count += 1
                at = text.find(term, at + len(term))
            title_at = title.find(term)
            score += (2 + (18 if term in project else 0) + (18 if term in customer else 0)
                      + (18 if 0 <= title_at < 20 else 12 if title_at >= 0 else 0)
                      + (8 if term in cwd else 0) + (1 + math.log(count) if count else 0))
        when = timestamp(record.get("updated_at"))
        days = (self.now - when) / 86400
        weight = 1 if not when else 1.3 if days <= 30 else 1.15 if days <= 90 else 1 if days <= 365 else .9
        return score * weight

    def _select(self, query, sources, days, sort, project):
        key = (query, tuple(sorted(sources)), days, sort, project)
        with self._lock:
            if key in self._matches:
                self._matches.move_to_end(key)
                return self._matches[key]
            terms, query_sources = parse_query(query)
            if terms:
                self._prepare()
            cutoff = self.now - days * 86400 if days else 0
            rows = []
            for r in self.records:
                if sources and r.get("source") not in sources:
                    continue
                if project is not None and (r.get("project") or "") != project:
                    continue
                if query_sources and not any(s in normalize(f"{r.get('source', '')} {r.get('source_label', '')}") for s in query_sources):
                    continue
                if cutoff and timestamp(r.get("updated_at")) < cutoff:
                    continue
                if terms and not all(t in self._folded[r["id"]] for t in terms):
                    continue
                rows.append(r)
            if sort == "relevance" and terms:
                rows.sort(key=lambda r: (-self._score(r, terms), -timestamp(r.get("updated_at")), r["id"]))
            else:
                rows.sort(key=lambda r: ((1 if sort == "oldest" else -1) * timestamp(r.get("updated_at")), r["id"]))
            self._matches[key] = (rows, terms)
            if len(self._matches) > 8:
                self._matches.popitem(last=False)
            return rows, terms

    def summary_record(self, record, terms=(), *, preview=True):
        # Whitelist metadata: parser additions cannot accidentally send another
        # full text field on list endpoints.
        keys = ("id", "source", "source_label", "title", "project", "customer", "cwd",
                "session_path", "updated_at", "created_at", "message_count", "origin",
                "manual", "manual_id", "manual_source", "open_scope", "project_source")
        result = {key: record[key] for key in keys if key in record}
        result["artifacts"] = (record.get("artifacts") or [])[:3]
        if preview:
            raw = str(record.get("excerpt") or "")
            result["excerpt"], result["excerpt_offset"] = snippet(raw, list(terms))
            result["excerpt_length"] = len(raw)
        return result

    def search(self, params: dict) -> dict:
        query = params.get("q", "")
        if len(query) > 4096:
            raise ValueError("query exceeds 4096 characters")
        view, sort, days = params.get("view", "sessions"), params.get("sort", "relevance"), params.get("range", "all")
        if view not in {"sessions", "projects"} or sort not in {"relevance", "recent", "oldest"} or days not in {"all", "30", "90", "365"}:
            raise ValueError("invalid search filter")
        offset = integer(params, "offset", 0, 2**53 - 1)
        limit = integer(params, "limit", PAGE_SIZE, MAX_PAGE_SIZE)
        sources = set(filter(None, params.get("source", "").split(",")))
        rows, terms = self._select(query, sources, 0 if days == "all" else int(days), sort, params.get("project"))
        groups = OrderedDict()
        for row in rows:
            groups.setdefault(row.get("project") or "", []).append(row)
        result = {"revision": self.revision, "total_records": len(rows), "total_projects": len(groups), "offset": offset}
        if view == "projects":
            # Ranked rows determine group order too. Unclassified stays last.
            items = sorted(groups.items(), key=lambda item: not bool(item[0]))
            result["groups"] = []
            for name, members in items[offset:offset + limit]:
                latest = max(members, key=lambda r: timestamp(r.get("updated_at")))
                workspace = next((r for r in members if r.get("cwd")), None)
                result["groups"].append({
                    "project": name, "total": len(members), "updated_at": latest.get("updated_at"),
                    "workspace_id": workspace["id"] if workspace else "",
                    "workspace_count": len({r["cwd"] for r in members if r.get("cwd")}),
                    "sources": list(dict.fromkeys(r["source"] for r in members)),
                    "records": [self.summary_record(r, preview=False) for r in members[:PROJECT_PREVIEW]],
                })
            total = len(items)
        else:
            result["records"] = [self.summary_record(r, terms) for r in rows[offset:offset + limit]]
            total = len(rows)
        result["total"] = total
        result["next_offset"] = offset + limit if offset + limit < total else None
        return result

    def detail(self, params: dict) -> dict:
        record = self.by_id.get(params.get("id"))
        if record is None:
            raise KeyError("record missing")
        offset = integer(params, "offset", 0, 2**53 - 1)
        limit = integer(params, "limit", TEXT_PAGE_SIZE, TEXT_PAGE_SIZE)
        raw = str(record.get("excerpt") or "")
        result = self.summary_record(record, preview=False)
        # Manual notes are required verbatim for editing. Existing local manual
        # input is bounded by the POST body limit; fetched only on explicit edit.
        if params.get("edit") == "1" and record.get("manual"):
            result["notes"] = str(record.get("notes") or "")
        return {"revision": self.revision, "record": result, "text": raw[offset:offset + limit],
                "offset": offset, "total_chars": len(raw),
                "next_offset": offset + limit if offset + limit < len(raw) else None}
