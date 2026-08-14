"""Reads local_backup/index.jsonl and derives "what's true right now" for the
web interface.

index.jsonl is an append-only log -- the same filename can appear multiple
times across pipeline runs (e.g. logged "uncertain" once, then approved on a
later run after a threshold change). This module collapses the log down to
the single most recent entry per filename, which is what the web interface
should actually show.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path


@dataclass
class ResultRecord:
    filename: str
    kind: str  # "photo" | "document" | "quarantine" | "deletion" | "uncertain"
    stored_at: str
    metadata: dict
    # Set only by load_latest_records_from_cosmos() -- a direct Blob Storage
    # URL, since the cloud-hosted dashboard has no local file to serve from
    # /media/*. Templates fall back to the local media route when this is
    # None (the local/Pi web interface).
    image_url: str | None = None


def load_latest_records(local_backup_dir: str | Path) -> dict[str, ResultRecord]:
    index_path = Path(local_backup_dir) / "index.jsonl"
    latest: dict[str, ResultRecord] = {}
    if not index_path.exists():
        return latest
    with open(index_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Manual split instead of Path(...).name: source_path may have
            # been logged on Windows (backslashes) but read back on Linux
            # (the Pi) or vice versa -- pathlib's separator handling is
            # platform-specific, so a plain string split is more portable.
            filename = raw["source_path"].replace("\\", "/").rsplit("/", 1)[-1]
            record = ResultRecord(
                filename=filename,
                kind=raw["kind"],
                stored_at=raw["stored_at"],
                metadata=raw.get("metadata", {}),
            )
            existing = latest.get(filename)
            if existing is None or record.stored_at >= existing.stored_at:
                latest[filename] = record
    return latest


# Maps AzureBackupStore's Cosmos `status` field back to the ResultRecord
# `kind` values the templates already know how to render. "approved" isn't
# listed here because it maps to `type` instead (photo vs document) --
# handled directly in load_latest_records_from_cosmos().
_STATUS_TO_KIND = {
    "quarantine": "quarantine",
    "deleted": "deletion",
    "review": "uncertain",
}

# Cosmos system fields that show up on every document -- stripped out when
# building ResultRecord.metadata so the web interface doesn't display
# Cosmos internals alongside the real photo metadata.
_COSMOS_SYSTEM_FIELDS = {"id", "filename", "batch_id", "status", "type", "stored_at", "blob_url",
                         "_rid", "_self", "_etag", "_attachments", "_ts"}


def load_latest_records_from_cosmos(container) -> dict[str, ResultRecord]:
    """Cloud equivalent of load_latest_records() -- used by the Azure-hosted
    dashboard, which has no local_backup/index.jsonl (no card is ever
    processed on the App Service itself). `container` is a Cosmos DB
    ContainerProxy for the same "photos" container AzureBackupStore writes
    to and the teammate's api/ code reads from."""
    latest: dict[str, ResultRecord] = {}
    for doc in container.query_items(query="SELECT * FROM c", enable_cross_partition_query=True):
        filename = doc.get("filename") or doc.get("id", "")
        if not filename:
            continue
        status = doc.get("status", "")
        kind = _STATUS_TO_KIND.get(status)
        if kind is None:
            kind = doc.get("type", "photo") if status == "approved" else "uncertain"
        stored_at = doc.get("stored_at", "") or ""
        metadata = {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_FIELDS}
        record = ResultRecord(
            filename=filename,
            kind=kind,
            stored_at=stored_at,
            metadata=metadata,
            image_url=doc.get("blob_url"),
        )
        existing = latest.get(filename)
        if existing is None or record.stored_at >= existing.stored_at:
            latest[filename] = record
    return latest


def summarize(records: dict[str, ResultRecord]) -> dict:
    counts = {"photo": 0, "document": 0, "quarantine": 0, "deletion": 0, "uncertain": 0}
    last_run = None
    for r in records.values():
        counts[r.kind] = counts.get(r.kind, 0) + 1
        if last_run is None or r.stored_at > last_run:
            last_run = r.stored_at
    return {"counts": counts, "last_run": last_run, "total": len(records)}


def filter_by_kind(records: dict[str, ResultRecord], kind: str) -> list[ResultRecord]:
    items = [r for r in records.values() if r.kind == kind]
    items.sort(key=lambda r: r.stored_at, reverse=True)
    return items


def _parse_exif_date(timestamp: str) -> date_cls | None:
    """EXIF timestamps look like "2013:07:27 21:59:13". Returns just the
    date portion, or None if the string doesn't parse (missing/malformed)."""
    if not timestamp:
        return None
    try:
        return datetime.strptime(timestamp[:10], "%Y:%m:%d").date()
    except ValueError:
        return None


def search_documents(
    records: dict[str, ResultRecord],
    query: str = "",
    date_from: str = "",
    date_to: str = "",
) -> list[ResultRecord]:
    """Text search over extracted_text/category, plus a real inclusive date
    range (date_from/date_to as "YYYY-MM-DD", e.g. from an HTML date input)
    against the photo's EXIF timestamp -- not a substring match, an actual
    range comparison."""
    docs = filter_by_kind(records, "document")
    query = query.strip().lower()

    parsed_from = _parse_iso_date(date_from)
    parsed_to = _parse_iso_date(date_to)

    results = []
    for r in docs:
        text = (r.metadata.get("extracted_text") or "").lower()
        category = (r.metadata.get("category") or "").lower()
        if query and query not in text and query not in category:
            continue

        if parsed_from or parsed_to:
            doc_date = _parse_exif_date(r.metadata.get("timestamp") or "")
            if doc_date is None:
                continue  # can't filter by date if we don't have one
            if parsed_from and doc_date < parsed_from:
                continue
            if parsed_to and doc_date > parsed_to:
                continue

        results.append(r)
    return results


def _parse_iso_date(value: str) -> date_cls | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
