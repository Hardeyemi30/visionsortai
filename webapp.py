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

import os
import subprocess
from pathlib import Path

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

app = Flask(__name__)
app.register_blueprint(photos_bp)
app.register_blueprint(backup_bp)
app.register_blueprint(audit_bp)
app.register_blueprint(batches_bp)
app.register_blueprint(quarantine_bp)


def is_cloud_deployment() -> bool:
    """True when running on Azure App Service, False everywhere else (the
    Pi, a dev machine). WEBSITE_SITE_NAME is set automatically by App
    Service itself -- nothing to configure, no risk of the Pi accidentally
    reading from Cosmos instead of its own local results."""
    return bool(os.getenv("WEBSITE_SITE_NAME"))


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
    """Picks the right data source automatically -- see is_cloud_deployment().
    In cloud mode without Cosmos credentials configured (AZURE_COSMOS_ENDPOINT/
    KEY missing from the App Service's Application Settings), degrades to an
    empty result set instead of crashing the whole dashboard with a 500."""
    if is_cloud_deployment():
        if not (CONFIG.azure_cosmos_endpoint and CONFIG.azure_cosmos_key):
            return {}
        try:
            return load_latest_records_from_cosmos(get_cosmos_container())
        except Exception:
            return {}
    return load_latest_records(backup_dir())


def backup_dir() -> Path:
    return Path(CONFIG.local_backup_dir)


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
    if record is None:
        abort(404)

    if record.image_url:
        display_url = record.image_url
    elif record.kind == "document":
        display_url = url_for("media_document", filename=record.filename)
    elif record.kind == "quarantine":
        display_url = url_for("media_quarantine", filename=record.filename)
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
    """Quarantined (bad, held for review) + deletions (duplicates, no copy
    kept) + uncertain items -- the audit trail."""
    records = load_records()
    quarantined = filter_by_kind(records, "quarantine")
    deletions = filter_by_kind(records, "deletion")
    uncertain = filter_by_kind(records, "uncertain")
    return render_template(
        "activity.html", quarantined=quarantined, deletions=deletions, uncertain=uncertain
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
