"""Orchestrates the full per-photo flow (see workflow diagram, steps 1-7).

    1. Trigger        -- caller passes in a photo path (from an SD card scan)
    2. Extract metadata -- exiftool (metadata.py)
    2.5 Dedup check    -- perceptual hash, resolved locally without the agent
    3. Local quality   -- blur / exposure (quality.py)
    3-4. AI agent      -- classification + structured output (agent.py)
    5. Routing         -- classification + confidence floor decide the action
    6-7. Act           -- delete / store document / backup photo (storage.py)

No human-review step by design (team decision) -- the confidence floor in
config.py (default 0.75) is the only safeguard against an irreversible wrong
delete: below that, the pipeline defaults to "keep" and logs the item as
uncertain instead of acting on it.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .agent import AgentResult, VisionAgent
from .config import Config
from .dedup import DuplicateIndex, compute_phash, resolve_duplicate
from .geocode import reverse_geocode
from .metadata import PhotoMetadata, extract_metadata
from .quality import (
    blur_score,
    exposure_metrics,
    is_bad_exposure,
)
from .storage import BackupRecord, BackupStore

Action = str  # "deleted_duplicate" | "quarantined" | "kept_uncertain" | "stored_document" | "stored_photo"


@dataclass
class PipelineResult:
    path: str
    action: Action
    detail: str
    agent_result: AgentResult | None = None
    backup_record: BackupRecord | None = None


class Pipeline:
    def __init__(
        self,
        config: Config,
        agent: VisionAgent,
        backup_store: BackupStore,
        dedup_index: DuplicateIndex | None = None,
        dry_run: bool = False,
        stop_file: str | Path | None = None,
    ):
        self.config = config
        self.agent = agent
        self.backup_store = backup_store
        self.dedup_index = dedup_index or DuplicateIndex(threshold=config.duplicate_hamming_threshold)
        self.dry_run = dry_run
        # Cooperative cancellation: if this file exists, process_card stops
        # before starting the next photo. Checked between photos rather than
        # killing the process outright, so nothing is left half-copied or
        # half-deleted. The web interface's "Stop" button just creates this
        # file -- no elevated privileges needed to control a systemd job.
        self.stop_file = Path(stop_file) if stop_file else None
        # One id per Pipeline instance -- in practice one per SD-card/USB
        # insert, since cli.py builds a fresh Pipeline per systemd run. Used
        # as the Cosmos DB partition key so the teammate's api/batches.py
        # can group every record from a single card-processing run.
        self.batch_id = str(uuid.uuid4())

    def stop_requested(self) -> bool:
        return self.stop_file is not None and self.stop_file.exists()

    def clear_stop_request(self) -> None:
        if self.stop_file is not None:
            self.stop_file.unlink(missing_ok=True)

    def _delete_file(self, path: Path) -> None:
        if self.dry_run:
            return
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def process_photo(self, path: Path) -> PipelineResult:
        path = Path(path)

        # Step 2: metadata (kept for logging/backup even though dedup uses
        # a perceptual hash, not the exif file hash)
        metadata: PhotoMetadata = extract_metadata(path, self.config.exiftool_binary)

        # Step 2.5: duplicate check (cheap, local, resolved without the agent)
        phash = compute_phash(path)
        blur = blur_score(path)
        existing = self.dedup_index.find_duplicate(phash)
        if existing is not None:
            resolution = resolve_duplicate(existing, path, blur)
            dedup_meta = {"batch_id": self.batch_id, "file_sha256": metadata.file_sha256}
            if resolution.delete_path == str(path):
                self.backup_store.log_deletion(path, reason="duplicate of an existing sharper photo", metadata=dedup_meta)
                self._delete_file(path)
                return PipelineResult(str(path), "deleted_duplicate", f"duplicate of {existing.path}")
            else:
                # The new photo is sharper -- it replaces the index entry,
                # and the previously-kept file is deleted instead.
                old_path = Path(existing.path)
                self.dedup_index.replace(existing, path, phash, blur)
                self.backup_store.log_deletion(old_path, reason="duplicate, superseded by a sharper photo", metadata=dedup_meta)
                self._delete_file(old_path)
                # fall through: the new (sharper) photo continues through quality/agent checks below
        else:
            self.dedup_index.add(path, phash, blur)

        # Step 3: local quality checks
        exposure = exposure_metrics(path)
        bad_exposure, exposure_reasons = is_bad_exposure(
            exposure,
            self.config.exposure_clip_pct_threshold,
            self.config.exposure_luminance_min,
            self.config.exposure_luminance_max,
        )
        local_scores = {
            "blur_score": blur,
            "bad_exposure": bad_exposure,
            "exposure_reasons": exposure_reasons,
            "mean_luminance": exposure.mean_luminance,
        }

        # Step 3-4: agent classification
        result = self.agent.classify(path, local_scores)

        # Step 5: routing
        floor = self.config.agent_confidence_floor
        meta_dict = {
            "batch_id": self.batch_id,
            "timestamp": metadata.timestamp,
            "camera_model": metadata.camera_model,
            "gps_latitude": metadata.gps_latitude,
            "gps_longitude": metadata.gps_longitude,
            "file_sha256": metadata.file_sha256,
            "agent_confidence": result.confidence,
            "agent_reasoning": result.reasoning,
        }

        # Resolved once here, at pipeline time, rather than lazily in the
        # web app on every page load -- a page with a dozen photos from the
        # same place would otherwise re-hit (and rate-limit against)
        # Nominatim on every single visit. Only real network cost is for a
        # genuinely new place; everything else is an instant cache hit (see
        # geocode.py). Left out of meta_dict entirely (not even as None)
        # when there's no GPS fix, or if the lookup couldn't be resolved --
        # templates already treat a missing location_label as "no place
        # name available" and fall back to raw coordinates.
        if metadata.gps_latitude is not None and metadata.gps_longitude is not None:
            label = reverse_geocode(metadata.gps_latitude, metadata.gps_longitude, self.config.local_backup_dir)
            if label:
                meta_dict["location_label"] = label

        if result.classification in ("duplicate", "bad"):
            if result.confidence >= floor:
                if result.classification == "bad":
                    # Held for review instead of deleted outright -- copied
                    # to quarantine storage (local folder / Azure blob
                    # "quarantine" container) so it's actually visible
                    # (locally in the web interface, and in the teammate's
                    # Cosmos-backed quarantine dashboard) rather than just a
                    # text log entry. Only removed from the SD card after
                    # it's safely quarantined elsewhere.
                    record = self.backup_store.quarantine_photo(path, reason=result.reasoning, metadata=meta_dict)
                    self._delete_file(path)
                    return PipelineResult(str(path), "quarantined", result.reasoning, result, record)
                else:
                    # "duplicate" classification from the agent is a
                    # defensive fallback -- real duplicate detection happens
                    # earlier via perceptual hashing (see the dedup
                    # fast-path above), which is what actually runs in
                    # practice. Duplicates keep auto-deleting immediately
                    # (team decision) since the sharper copy is always kept.
                    self.backup_store.log_deletion(path, reason=result.reasoning, metadata=meta_dict)
                    self._delete_file(path)
                    return PipelineResult(str(path), "deleted_duplicate", result.reasoning, result)
            else:
                record = self.backup_store.log_uncertain(
                    path, reason=f"low confidence ({result.confidence:.2f}) for '{result.classification}'",
                    metadata=meta_dict,
                )
                return PipelineResult(str(path), "kept_uncertain", record.metadata["reason"], result, record)

        if result.classification == "document":
            meta_dict.update({
                "extracted_text": result.extracted_text,
                "category": result.category,
            })
            record = self.backup_store.upload_document_record(path, meta_dict)
            return PipelineResult(str(path), "stored_document", f"category={result.category}", result, record)

        # classification == "photo"
        record = self.backup_store.upload_photo(path, meta_dict)
        return PipelineResult(str(path), "stored_photo", "approved photo", result, record)

    def process_card(self, mount_path: Path, extensions=(".jpg", ".jpeg", ".png")) -> list[PipelineResult]:
        mount_path = Path(mount_path)
        self.clear_stop_request()  # discard any stale request from a previous run
        results = []
        for entry in sorted(mount_path.rglob("*")):
            if self.stop_requested():
                print(f"Stop requested -- halting after {len(results)} photo(s), before {entry.name}.")
                break
            if entry.is_file() and entry.suffix.lower() in extensions:
                results.append(self.process_photo(entry))
        self.clear_stop_request()
        return results
