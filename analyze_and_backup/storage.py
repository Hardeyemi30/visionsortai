"""Step 6-7 of the pipeline: backup / persistence.

BackupStore is the interface the pipeline codes against. Two implementations:
  - AzureBackupStore: uploads approved photos to Azure Blob Storage, bad
    photos to a separate "quarantine" blob container (instead of deleting
    them outright), and writes a searchable Cosmos DB index using the same
    database/container the teammate's api/ dashboard code (photos.py,
    batches.py, quarantine.py, audit.py) already queries -- so records
    written here show up there with no extra glue code. Falls back to a
    local JSON-lines log if Cosmos credentials aren't configured.
  - LocalBackupStore: pure local-filesystem implementation for dev/testing
    without any Azure account at all. Also quarantines instead of deleting
    (copies to local_backup/quarantine/), so bad/duplicate photos are
    visible in the local web interface instead of just a text log entry.
"""
from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


@dataclass
class BackupRecord:
    kind: str  # "photo" | "document" | "quarantine" | "duplicate" | "deletion" | "uncertain"
    source_path: str
    stored_at: str  # ISO timestamp
    metadata: dict


class BackupStore(Protocol):
    def upload_photo(self, path: Path, metadata: dict) -> BackupRecord: ...
    def upload_document_record(self, path: Path, metadata: dict) -> BackupRecord: ...
    def quarantine_photo(self, path: Path, reason: str, metadata: dict) -> BackupRecord: ...
    def remove_duplicate(self, path: Path, reason: str, metadata: dict) -> BackupRecord: ...
    def log_deletion(self, path: Path, reason: str, metadata: dict | None = None) -> BackupRecord: ...
    def log_uncertain(self, path: Path, reason: str, metadata: dict | None = None) -> BackupRecord: ...
    def delete_stored(self, kind: str, filename: str, reason: str, metadata: dict | None = None) -> BackupRecord: ...


class LocalBackupStore:
    """Writes files into local_backup/{photos,documents}/ and appends every
    action to local_backup/index.jsonl. Used for tests and offline dev runs."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        (self.base_dir / "photos").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "documents").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "quarantine").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "duplicates").mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "index.jsonl"

    def _append_index(self, record: BackupRecord) -> None:
        with open(self.index_path, "a") as f:
            f.write(json.dumps(asdict(record)) + "\n")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def upload_photo(self, path: Path, metadata: dict) -> BackupRecord:
        path = Path(path)
        dest = self.base_dir / "photos" / path.name
        shutil.copy2(path, dest)
        record = BackupRecord("photo", str(path), self._now(), metadata)
        self._append_index(record)
        return record

    def upload_document_record(self, path: Path, metadata: dict) -> BackupRecord:
        path = Path(path)
        dest = self.base_dir / "documents" / path.name
        shutil.copy2(path, dest)
        record = BackupRecord("document", str(path), self._now(), metadata)
        self._append_index(record)
        return record

    def quarantine_photo(self, path: Path, reason: str, metadata: dict) -> BackupRecord:
        path = Path(path)
        dest = self.base_dir / "quarantine" / path.name
        shutil.copy2(path, dest)
        record = BackupRecord("quarantine", str(path), self._now(), {**metadata, "reason": reason})
        self._append_index(record)
        return record

    def remove_duplicate(self, path: Path, reason: str, metadata: dict) -> BackupRecord:
        """A pipeline-detected duplicate -- like quarantine_photo, a copy is
        kept (in duplicates/) before the source is deleted, so removed
        photos are still visible in the web interface instead of just a
        filename + reason in the log. Unlike quarantine there's no
        approve/restore step: the sharper copy was always kept, so nothing
        unique is at risk and the delete still happens immediately -- this
        copy is purely for visibility/audit, not a review queue."""
        path = Path(path)
        dest = self.base_dir / "duplicates" / path.name
        shutil.copy2(path, dest)
        record = BackupRecord("duplicate", str(path), self._now(), {**metadata, "reason": reason})
        self._append_index(record)
        return record

    def log_deletion(self, path: Path, reason: str, metadata: dict | None = None) -> BackupRecord:
        record = BackupRecord("deletion", str(path), self._now(), {**(metadata or {}), "reason": reason})
        self._append_index(record)
        return record

    def log_uncertain(self, path: Path, reason: str, metadata: dict | None = None) -> BackupRecord:
        record = BackupRecord("uncertain", str(path), self._now(), {**(metadata or {}), "reason": reason})
        self._append_index(record)
        return record

    # Kind -> the folder it's actually sitting in under base_dir, for a
    # user-initiated delete from the web interface (as opposed to
    # log_deletion() above, which is the pipeline discarding a duplicate
    # that was never stored in the first place).
    _KIND_FOLDER = {"photo": "photos", "document": "documents", "quarantine": "quarantine", "duplicate": "duplicates"}

    def delete_stored(self, kind: str, filename: str, reason: str, metadata: dict | None = None) -> BackupRecord:
        folder = self._KIND_FOLDER.get(kind)
        if folder is None:
            raise ValueError(f"delete_stored: unsupported kind {kind!r}")
        target = self.base_dir / folder / filename
        target.unlink(missing_ok=True)
        record = BackupRecord("deletion", str(target), self._now(), {**(metadata or {}), "reason": reason})
        self._append_index(record)
        return record


class AzureBackupStore:
    """Uploads approved photos/documents to Blob Storage, bad photos to a
    separate quarantine container, and indexes everything in Cosmos DB using
    the schema the teammate's api/ dashboard code already queries:
    top-level `id` / `filename` / `batch_id` / `status` fields (status is
    one of "approved" | "quarantine" | "review" | "deleted", matching his
    quarantine.py/batches.py), plus a `type` field ("photo" | "document")
    since his single `photos` container holds both. `batch_id` doubles as
    the Cosmos partition key (see his quarantine.py delete_photo), so every
    write includes it.
    """

    def __init__(
        self,
        connection_string: str,
        container: str,
        quarantine_container: str = "quarantine",
        duplicates_container: str = "duplicates",
        cosmos_endpoint: str | None = None,
        cosmos_key: str | None = None,
        cosmos_database: str = "visionsortai",
        cosmos_container: str = "photos",
    ):
        from azure.storage.blob import BlobServiceClient

        self._client = BlobServiceClient.from_connection_string(connection_string)
        self._container = container
        self._container_client = self._client.get_container_client(container)
        try:
            self._container_client.create_container()
        except Exception:
            pass  # already exists

        self._quarantine_client = self._client.get_container_client(quarantine_container)
        try:
            self._quarantine_client.create_container()
        except Exception:
            pass  # already exists

        self._duplicates_client = self._client.get_container_client(duplicates_container)
        try:
            self._duplicates_client.create_container()
        except Exception:
            pass  # already exists

        # Cosmos is optional: without endpoint/key, writes fall back to a
        # local JSON-lines log so this class still works end-to-end (and is
        # testable) without a Cosmos account configured.
        self._cosmos_container = None
        if cosmos_endpoint and cosmos_key:
            from azure.cosmos import CosmosClient

            cosmos_client = CosmosClient(cosmos_endpoint, cosmos_key)
            database = cosmos_client.get_database_client(cosmos_database)
            self._cosmos_container = database.get_container_client(cosmos_container)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _upload_blob(self, path: Path, container_client, blob_prefix: str) -> str:
        path = Path(path)
        blob_name = f"{blob_prefix}/{path.name}" if blob_prefix else path.name
        with open(path, "rb") as data:
            container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        return container_client.get_blob_client(blob_name).url

    def _cosmos_id(self, metadata: dict) -> str:
        # file_sha256 is stable across re-runs of the same file, so writes
        # are idempotent (upsert_item) instead of piling up duplicate
        # documents if a photo gets processed more than once.
        return metadata.get("file_sha256") or str(uuid.uuid4())

    def _write_index_record(self, record: BackupRecord, cosmos_fields: dict) -> None:
        if self._cosmos_container is not None:
            self._cosmos_container.upsert_item(cosmos_fields)
        else:
            # TODO(teammate): once AZURE_COSMOS_ENDPOINT/KEY are set this
            # path is never used -- this fallback just keeps the class
            # testable/runnable without a Cosmos account.
            fallback = Path("azure_index_fallback.jsonl")
            with open(fallback, "a") as f:
                f.write(json.dumps(asdict(record)) + "\n")

    def upload_photo(self, path: Path, metadata: dict) -> BackupRecord:
        path = Path(path)
        url = self._upload_blob(path, self._container_client, "photos")
        record = BackupRecord("photo", str(path), self._now(), {**metadata, "blob_url": url})
        self._write_index_record(record, {
            **metadata,
            "id": self._cosmos_id(metadata),
            "filename": path.name,
            "batch_id": metadata.get("batch_id"),
            "status": "approved",
            "type": "photo",
            "blob_url": url,
            "stored_at": record.stored_at,
        })
        return record

    def upload_document_record(self, path: Path, metadata: dict) -> BackupRecord:
        path = Path(path)
        url = self._upload_blob(path, self._container_client, "documents")
        record = BackupRecord("document", str(path), self._now(), {**metadata, "blob_url": url})
        self._write_index_record(record, {
            **metadata,
            "id": self._cosmos_id(metadata),
            "filename": path.name,
            "batch_id": metadata.get("batch_id"),
            "status": "approved",
            "type": "document",
            "blob_url": url,
            "stored_at": record.stored_at,
        })
        return record

    def quarantine_photo(self, path: Path, reason: str, metadata: dict) -> BackupRecord:
        path = Path(path)
        # No prefix here, unlike photos/documents -- those share one
        # "analyze-and-backup" container so a folder prefix separates them.
        # Quarantine already has its own dedicated container, so a prefix
        # would just double up in the blob path (container/quarantine/... vs
        # just container/...).
        url = self._upload_blob(path, self._quarantine_client, "")
        record = BackupRecord("quarantine", str(path), self._now(), {**metadata, "reason": reason, "blob_url": url})
        self._write_index_record(record, {
            **metadata,
            "id": self._cosmos_id(metadata),
            "filename": path.name,
            "batch_id": metadata.get("batch_id"),
            "status": "quarantine",
            "type": "photo",
            "reason": reason,
            "blob_url": url,
            "stored_at": record.stored_at,
        })
        return record

    def remove_duplicate(self, path: Path, reason: str, metadata: dict) -> BackupRecord:
        path = Path(path)
        # No prefix, same reasoning as quarantine_photo -- duplicates has
        # its own dedicated container.
        url = self._upload_blob(path, self._duplicates_client, "")
        record = BackupRecord("duplicate", str(path), self._now(), {**metadata, "reason": reason, "blob_url": url})
        self._write_index_record(record, {
            **metadata,
            "id": self._cosmos_id(metadata),
            "filename": path.name,
            "batch_id": metadata.get("batch_id"),
            "status": "duplicate",
            "type": "photo",
            "reason": reason,
            "blob_url": url,
            "stored_at": record.stored_at,
        })
        return record

    def log_deletion(self, path: Path, reason: str, metadata: dict | None = None) -> BackupRecord:
        metadata = metadata or {}
        path = Path(path)
        record = BackupRecord("deletion", str(path), self._now(), {**metadata, "reason": reason})
        self._write_index_record(record, {
            **metadata,
            "id": self._cosmos_id(metadata),
            "filename": path.name,
            "batch_id": metadata.get("batch_id"),
            "status": "deleted",
            "type": "photo",
            "reason": reason,
            "stored_at": record.stored_at,
        })
        return record

    def log_uncertain(self, path: Path, reason: str, metadata: dict | None = None) -> BackupRecord:
        metadata = metadata or {}
        path = Path(path)
        record = BackupRecord("uncertain", str(path), self._now(), {**metadata, "reason": reason})
        self._write_index_record(record, {
            **metadata,
            "id": self._cosmos_id(metadata),
            "filename": path.name,
            "batch_id": metadata.get("batch_id"),
            "status": "review",
            "type": "photo",
            "reason": reason,
            "stored_at": record.stored_at,
        })
        return record

    # kind -> (which blob container it was uploaded to, the folder prefix
    # used in the blob name) -- mirrors the prefixes upload_photo() /
    # upload_document_record() / quarantine_photo() actually wrote with
    # above, so a user-initiated delete from the web interface removes the
    # exact blob that's really there.
    def _blob_location(self, kind: str):
        return {
            "photo": (self._container_client, "photos"),
            "document": (self._container_client, "documents"),
            "quarantine": (self._quarantine_client, ""),
            "duplicate": (self._duplicates_client, ""),
        }.get(kind, (None, None))

    def delete_stored(self, kind: str, filename: str, reason: str, metadata: dict | None = None) -> BackupRecord:
        metadata = metadata or {}
        client, prefix = self._blob_location(kind)
        if client is None:
            raise ValueError(f"delete_stored: unsupported kind {kind!r}")

        blob_name = f"{prefix}/{filename}" if prefix else filename
        try:
            client.delete_blob(blob_name)
        except Exception:
            pass  # already gone, or never made it to blob storage -- still record the deletion below

        record = BackupRecord("deletion", filename, self._now(), {**metadata, "reason": reason})
        # Reusing the same Cosmos id (derived from file_sha256, same as
        # every other write here) means this upsert replaces the existing
        # "approved"/"quarantine" document in place instead of leaving a
        # stale duplicate behind -- falls back to a fresh id only if the
        # caller couldn't supply the original metadata (e.g. file_sha256
        # missing), in which case the old doc is orphaned but the deletion
        # still gets recorded.
        self._write_index_record(record, {
            **metadata,
            "id": self._cosmos_id(metadata),
            "filename": filename,
            "batch_id": metadata.get("batch_id"),
            "status": "deleted",
            "type": "document" if kind == "document" else "photo",
            "reason": reason,
            "stored_at": record.stored_at,
        })
        return record
