"""Central configuration and default thresholds.

Threshold defaults come from the calibration discussion in the project docs:
- Duplicate detection: 64-bit perceptual hash (dHash/pHash), Hamming distance <= 8
- Blur: Laplacian variance, starting point 100.0 -- MUST be recalibrated against
  real sample photos from the Pi's actual camera (see README "Calibrating
  thresholds"). This default is a placeholder, not a measured value.
- Exposure: >5% of pixels clipped at 0 or 255, or mean luminance outside 40-215
- Agent confidence floor: only auto-act (delete / file as document) when the
  vision agent reports confidence >= 0.75. Below that, default to "keep" and
  log as uncertain -- there is no human review step, so this floor is the only
  safety net against an irreversible wrong delete.

All values are overridable via environment variables (loaded from .env).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _float_env(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default


def _int_env(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val not in (None, "") else default


@dataclass(frozen=True)
class Config:
    # --- Thresholds ---
    duplicate_hamming_threshold: int = _int_env("DUPLICATE_HAMMING_THRESHOLD", 8)
    blur_variance_threshold: float = _float_env("BLUR_VARIANCE_THRESHOLD", 100.0)
    exposure_clip_pct_threshold: float = _float_env("EXPOSURE_CLIP_PCT_THRESHOLD", 0.05)
    exposure_luminance_min: float = _float_env("EXPOSURE_LUMINANCE_MIN", 40.0)
    exposure_luminance_max: float = _float_env("EXPOSURE_LUMINANCE_MAX", 215.0)
    agent_confidence_floor: float = _float_env("AGENT_CONFIDENCE_FLOOR", 0.75)

    # --- exiftool ---
    exiftool_binary: str = os.getenv("EXIFTOOL_BINARY", "exiftool")

    # --- Azure OpenAI (vision agent) ---
    azure_openai_endpoint: str | None = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_openai_key: str | None = os.getenv("AZURE_OPENAI_KEY")
    azure_openai_deployment: str | None = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    azure_openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

    # --- Azure Storage ---
    azure_storage_connection_string: str | None = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    azure_storage_container: str = os.getenv("AZURE_STORAGE_CONTAINER", "analyze-and-backup")
    azure_quarantine_container: str = os.getenv("AZURE_QUARANTINE_CONTAINER", "quarantine")
    azure_duplicates_container: str = os.getenv("AZURE_DUPLICATES_CONTAINER", "duplicates")

    # --- Azure Cosmos DB (shared searchable index -- same database/container
    # the teammate's api/ dashboard code (photos.py, batches.py,
    # quarantine.py, audit.py) reads from. Optional: if endpoint/key aren't
    # set, AzureBackupStore falls back to a local JSON-lines log instead. ---
    azure_cosmos_endpoint: str | None = os.getenv("AZURE_COSMOS_ENDPOINT")
    azure_cosmos_key: str | None = os.getenv("AZURE_COSMOS_KEY")
    azure_cosmos_database: str = os.getenv("AZURE_COSMOS_DATABASE", "visionsortai")
    azure_cosmos_container: str = os.getenv("AZURE_COSMOS_CONTAINER", "photos")

    # --- Misc ---
    sd_card_mount_path: str = os.getenv("SD_CARD_MOUNT_PATH", "/media/pi/SD_CARD")
    local_backup_dir: str = os.getenv("LOCAL_BACKUP_DIR", "./local_backup")
    # Coordination file for the web interface's "Stop" button (see pipeline.py
    # stop_requested()). Lives next to local_backup_dir regardless of which
    # BackupStore backend is in use -- it's just a signal, not backup data.
    stop_file_path: str = os.getenv("STOP_FILE_PATH", "./local_backup/STOP_REQUESTED")


CONFIG = Config()
