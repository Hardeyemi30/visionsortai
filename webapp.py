#!/usr/bin/env python3
"""Web interface -- shows what the pipeline did: which photos were kept,
which documents were found and what they say, what got quarantined/removed,
and basic run stats. Runs in two modes, auto-detected:

  - Local (the Pi): reads straight from local_backup/, refreshed on every
    request (no caching), so it always reflects the latest pipeline run on
    that specific device.
  - Cloud (Azure App Service): no local_backup/ exists there -- reads from
    Cosmos DB / Blob Storage instead, the same shared data source the
    teammate's api/ blueprints (also registered here) read from.

Run locally:
    python webapp.py
Then visit http://<pi-hostname>.local:5000 on any device on the same network
(see generate_qr.py for a printable QR code pointing at that address).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, abort, redirect, render_template, request, send_from_directory, url_for

from analyze_and_backup.config import CONFIG
from analyze_and_backup.results import (
    browse_photos,
    filter_by_kind,
    load_latest_records,
    load_latest_records_from_cosmos,
    search_documents,
    summarize,
)

from api.audit import audit_bp
from api.backup import backup_bp
from api.batches import batches_bp
from api.photos import photos_bp
from api.quarantine import quarantine_bp

# cli.py is what the udev-triggered systemd unit runs for an inserted SD
# card -- importing its build_pipeline() here means photos uploaded through
# the website go through the exact same Pipeline construction (same real
# Azure agent/backup store when configured, same mock/local dev fallback
# when it isn't) instead of a second, possibly-drifting copy of that logic.
import cli

app = Flask(__name__)
app.register_blueprint(photos_bp)
app.register_blueprint(backup_bp)
app.register_blueprint(audit_bp)
app.register_blueprint(batches_bp)
app.register_blueprint(quarantine_bp)

# Guards against someone accidentally selecting a huge batch of RAW/video
# files -- 50MB is generous for a handful of JPEGs/PNGs while still capping
# how long one upload can tie up the pipeline (and the Pi's limited RAM).
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

UPLOAD_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@app.context_processor
def inject_active_page():
    """Lets base.html highlight the current nav pill without every route
    passing active_page= explicitly."""
    return {"active_page": request.endpoint}


_cosmos_container = None


def get_cosmos_container():
    """Lazily builds (and caches) the Cosmos container client used to read
    records in cloud mode -- same database/container AzureBackupStore
    writes to and the teammate's api/ blueprints query."""
    global _cosmos_container
    if _cosmos_container is None:
        from azure.cosmos import CosmosClient

        client = CosmosClient(CONFIG.azure_cosmos_endpoint, CONFIG.azure_cosmos_key)
        database = client.get_database_client(CONFIG.azure_cosmos_database)
        _cosmos_container = database.get_container_client(CONFIG.azure_cosmos_container)
    return _cosmos_container


def load_records():
    """Reads from Cosmos DB whenever credentials are configured, regardless
    of whether this is running on Azure App Service or on the Pi itself --
    both write to and read from the same shared container once
    AZURE_COSMOS_ENDPOINT/KEY are set. This keeps the Pi's own local
    dashboard in sync now that the pipeline writes straight to Cosmos/Blob
    instead of local_backup/. Falls back to local_backup/index.jsonl only
    when Cosmos isn't configured at all (e.g. --local-backup/mock dev runs),
    and degrades to an empty result set instead of crashing with a 500 if
    Cosmos is configured but unreachable."""
    if CONFIG.azure_cosmos_endpoint and CONFIG.azure_cosmos_key:
        try:
            return load_latest_records_from_cosmos(get_cosmos_container())
        except Exception:
            return {}
    return load_latest_records(backup_dir())


def backup_dir() -> Path:
    return Path(CONFIG.local_backup_dir)


def _get_backup_store():
    """Same real-vs-local choice as _build_upload_pipeline()/cli.py's
    build_pipeline() -- deleting a stored photo/document has to go through
    whichever backend actually stored it (Azure Blob + Cosmos in
    production, local_backup/ + index.jsonl in dev), or the delete would
    silently no-op against the wrong place."""
    if CONFIG.azure_storage_connection_string:
        from analyze_and_backup.storage import AzureBackupStore

        return AzureBackupStore(
            connection_string=CONFIG.azure_storage_connection_string,
            container=CONFIG.azure_storage_container,
            quarantine_container=CONFIG.azure_quarantine_container,
            duplicates_container=CONFIG.azure_duplicates_container,
            cosmos_endpoint=CONFIG.azure_cosmos_endpoint,
            cosmos_key=CONFIG.azure_cosmos_key,
            cosmos_database=CONFIG.azure_cosmos_database,
            cosmos_container=CONFIG.azure_cosmos_container,
        )
    from analyze_and_backup.storage import LocalBackupStore

    return LocalBackupStore(CONFIG.local_backup_dir)


def stop_file() -> Path:
    return Path(CONFIG.stop_file_path)


def is_run_active() -> bool:
    """Checks (read-only, no elevated privileges needed) whether a
    analyze-and-backup@*.service instance is currently running. Degrades to
    "unknown -> False" if systemctl isn't available at all, e.g. when
    running this on a dev machine instead of the actual Pi."""
    try:
        proc = subprocess.run(
            ["systemctl", "list-units", "analyze-and-backup@*.service",
             "--state=running,activating", "--no-legend", "--plain"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(proc.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _safe_media_path(folder: Path, filename: str) -> Path:
    """Resolve filename under folder, refusing any path-traversal attempt."""
    target = (folder / filename).resolve()
    if not target.is_relative_to(folder.resolve()):
        abort(403)
    return target


# Cosmetic-only mapping -- which icon/tone the dashboard activity feed uses
# per record kind. Kept here (not in results.py) since it's presentation,
# not data.
_FEED_STYLE = {
    "photo": ("good", "check"),
    "document": ("doc", "doc"),
    "quarantine": ("warn", "warn"),
    "duplicate": ("bad", "warn"),
    "deletion": ("bad", "warn"),
    "uncertain": ("warn", "warn"),
}


def _build_activity_feed(records: dict, limit: int = 8) -> list[dict]:
    """Merges every record kind into one reverse-chronological feed for the
    dashboard's "Activity" panel -- what actually happened recently, not
    just a static count."""
    items = sorted(records.values(), key=lambda r: r.stored_at, reverse=True)[:limit]
    feed = []
    for r in items:
        tone, icon = _FEED_STYLE.get(r.kind, ("good", "check"))
        if r.kind == "photo":
            title, subtitle = "Photo kept", r.metadata.get("location_label") or r.filename
        elif r.kind == "document":
            title = f"Document filed — {r.metadata.get('category') or 'uncategorized'}"
            subtitle = r.filename
        elif r.kind == "quarantine":
            title, subtitle = "Quarantined for review", r.metadata.get("reason") or r.filename
        elif r.kind == "duplicate":
            title, subtitle = "Duplicate removed", r.metadata.get("reason") or r.filename
        elif r.kind == "deletion":
            title, subtitle = "Removed by you", r.metadata.get("reason") or r.filename
        else:
            title, subtitle = "Kept, low confidence", r.metadata.get("reason") or r.filename
        feed.append({"tone": tone, "icon": icon, "title": title, "subtitle": subtitle, "stored_at": r.stored_at})
    return feed


def _top_locations(records: dict, limit: int = 6) -> list[dict]:
    """Distinct geocoded place names across kept photos, most-photographed
    first -- what the dashboard's "Where these were taken" panel shows,
    since a raw pin-scatter map isn't worth building for a project this
    size but real place names (already computed once per photo at pipeline
    time, see geocode.py) are."""
    counts: dict[str, dict] = {}
    for r in filter_by_kind(records, "photo"):
        label = r.metadata.get("location_label")
        if not label:
            continue
        entry = counts.setdefault(label, {"label": label, "count": 0, "maps_url": None})
        entry["count"] += 1
        if entry["maps_url"] is None:
            lat = r.metadata.get("gps_latitude")
            lng = r.metadata.get("gps_longitude")
            if lat is not None and lng is not None:
                entry["maps_url"] = f"https://www.google.com/maps?q={lat},{lng}"
    return sorted(counts.values(), key=lambda e: e["count"], reverse=True)[:limit]


@app.route("/")
def dashboard():
    records = load_records()
    summary = summarize(records)
    recent_photos = filter_by_kind(records, "photo")[:12]
    recent_documents = filter_by_kind(records, "document")[:6]
    run_active = is_run_active()
    return render_template(
        "dashboard.html",
        summary=summary,
        recent_photos=recent_photos,
        recent_documents=recent_documents,
        activity_feed=_build_activity_feed(records),
        locations=_top_locations(records),
        run_active=run_active,
        stop_requested=run_active and stop_file().exists(),
    )


@app.route("/control/stop", methods=["POST"])
def control_stop():
    """Drops a signal file the running pipeline checks between photos and
    halts on -- see Pipeline.stop_requested() in pipeline.py. We deliberately
    don't call systemctl/sudo from here: touching a file needs no elevated
    privileges, so the web app never needs root."""
    sf = stop_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.touch(exist_ok=True)
    return redirect(url_for("dashboard"))


def _build_upload_pipeline():
    """Mirrors cli.py's own argument choices for the systemd-triggered SD
    card run: the real Azure vision agent + real Azure Blob/Cosmos storage
    when they're configured, falling back to the offline mock agent +
    local_backup/ otherwise (e.g. testing on a dev machine with no Azure
    account at all). Uploaded photos end up treated identically to ones
    that arrive via an inserted card."""
    has_azure_agent = bool(
        CONFIG.azure_openai_endpoint and CONFIG.azure_openai_key and CONFIG.azure_openai_deployment
    )
    has_azure_storage = bool(CONFIG.azure_storage_connection_string)
    args = SimpleNamespace(
        mock_agent=not has_azure_agent,
        local_backup=not has_azure_storage,
        dry_run=False,
    )
    return cli.build_pipeline(args)


@app.route("/upload", methods=["GET", "POST"])
def upload():
    """Lets someone add photos through the website instead of only via a
    physical SD card/USB drive -- same Pipeline, same routing (kept /
    quarantined / filed as a document / deduped)."""
    if request.method == "GET":
        return render_template("upload.html")

    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        return render_template("upload.html", error="Choose at least one photo first.")

    incoming_root = backup_dir() / "uploads_incoming"
    incoming_root.mkdir(parents=True, exist_ok=True)
    incoming_dir = Path(tempfile.mkdtemp(prefix=f"{uuid.uuid4().hex}_", dir=incoming_root))

    saved = 0
    skipped = 0
    try:
        for f in files:
            ext = Path(f.filename).suffix.lower()
            if ext not in UPLOAD_ALLOWED_EXTENSIONS:
                skipped += 1
                continue
            f.save(incoming_dir / f"{uuid.uuid4().hex}{ext}")
            saved += 1

        if saved == 0:
            return render_template(
                "upload.html",
                error="Only .jpg/.jpeg/.png files are supported -- none of the selected files matched.",
            )

        pipeline = _build_upload_pipeline()
        results = pipeline.process_card(incoming_dir)
    finally:
        shutil.rmtree(incoming_dir, ignore_errors=True)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.action] = counts.get(r.action, 0) + 1

    return render_template(
        "upload.html",
        done=True,
        total=len(results),
        skipped=skipped,
        counts=counts,
        results=results,
    )


@app.errorhandler(413)
def upload_too_large(_e):
    return render_template(
        "upload.html",
        error="That upload was too large (50MB limit) -- try fewer photos at a time.",
    ), 413


@app.route("/photos")
def photos():
    records = load_records()
    sort = request.args.get("sort", "newest")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    location = request.args.get("location", "")
    all_photos = browse_photos(records, sort=sort, date_from=date_from, date_to=date_to, location=location)
    return render_template(
        "photos.html",
        photos=all_photos,
        sort=sort,
        date_from=date_from,
        date_to=date_to,
        location=location,
    )


@app.route("/detail/<path:filename>")
def detail(filename):
    """Full metadata for one photo/document/quarantined item -- what
    clicking a thumbnail opens, instead of just the raw image."""
    records = load_records()
    record = records.get(filename)
    if record is None or record.kind not in ("photo", "document", "quarantine", "duplicate"):
        # A filename's most recent index record can be a "deletion" or
        # "uncertain" log entry instead of the viewable item itself (e.g.
        # right after someone deletes it via the web UI) -- treat those the
        # same as not found rather than rendering a broken page with a dead
        # image link and deletion-log metadata mislabeled as the photo.
        abort(404)

    if record.image_url:
        display_url = record.image_url
    elif record.kind == "document":
        display_url = url_for("media_document", filename=record.filename)
    elif record.kind == "quarantine":
        display_url = url_for("media_quarantine", filename=record.filename)
    elif record.kind == "duplicate":
        display_url = url_for("media_duplicate", filename=record.filename)
    else:
        display_url = url_for("media_photo", filename=record.filename)

    maps_url = None
    lat = record.metadata.get("gps_latitude")
    lng = record.metadata.get("gps_longitude")
    if lat is not None and lng is not None:
        maps_url = f"https://www.google.com/maps?q={lat},{lng}"

    return render_template("detail.html", record=record, display_url=display_url, maps_url=maps_url)


@app.route("/documents")
def documents():
    records = load_records()
    query = request.args.get("q", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    results = search_documents(records, query=query, date_from=date_from, date_to=date_to)
    return render_template(
        "documents.html",
        documents=results,
        query=query,
        date_from=date_from,
        date_to=date_to,
    )


@app.route("/activity")
def activity():
    """Quarantined (bad, held for review) + duplicates (auto-removed by the
    pipeline, copy kept for visibility) + uncertain items + deletions
    (manually removed via the web interface, no copy kept) -- the audit
    trail."""
    records = load_records()
    quarantined = filter_by_kind(records, "quarantine")
    duplicates = filter_by_kind(records, "duplicate")
    deletions = filter_by_kind(records, "deletion")
    uncertain = filter_by_kind(records, "uncertain")
    return render_template(
        "activity.html", quarantined=quarantined, duplicates=duplicates, deletions=deletions, uncertain=uncertain
    )


@app.route("/media/photos/<path:filename>")
def media_photo(filename):
    folder = backup_dir() / "photos"
    _safe_media_path(folder, filename)
    return send_from_directory(folder, filename)


@app.route("/media/documents/<path:filename>")
def media_document(filename):
    folder = backup_dir() / "documents"
    _safe_media_path(folder, filename)
    return send_from_directory(folder, filename)


@app.route("/media/quarantine/<path:filename>")
def media_quarantine(filename):
    folder = backup_dir() / "quarantine"
    _safe_media_path(folder, filename)
    return send_from_directory(folder, filename)


@app.route("/media/duplicates/<path:filename>")
def media_duplicate(filename):
    folder = backup_dir() / "duplicates"
    _safe_media_path(folder, filename)
    return send_from_directory(folder, filename)


_DELETABLE_KINDS = {"photo", "document", "quarantine", "duplicate"}


@app.route("/delete/<path:filename>", methods=["POST"])
def delete_record(filename):
    """Lets someone remove a kept photo/document/quarantined item straight
    from the web interface -- not just hide it, actually deletes the stored
    file/blob and logs a "deletion" record (same convention the pipeline
    already uses for duplicates), so it drops out of Photos/Documents and
    shows up under Activity > Removed instead."""
    records = load_records()
    record = records.get(filename)
    if record is None or record.kind not in _DELETABLE_KINDS:
        abort(404)

    store = _get_backup_store()
    store.delete_stored(record.kind, filename, reason="removed by user via web interface", metadata=record.metadata)

    next_url = request.form.get("next") or url_for("dashboard")
    return redirect(next_url)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
