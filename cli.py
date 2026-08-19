#!/usr/bin/env python3
"""Entry point: scan an SD card mount path and run the full pipeline on it.

Examples:
    # Real run against Azure (needs .env configured)
    python cli.py --mount-path /media/pi/SD_CARD

    # Local dry run, no Azure account needed at all
    python cli.py --mount-path ./sample_photos --mock-agent --local-backup --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from analyze_and_backup.agent import AzureVisionAgent, MockVisionAgent
from analyze_and_backup.config import CONFIG
from analyze_and_backup.pipeline import Pipeline
from analyze_and_backup.storage import AzureBackupStore, LocalBackupStore


def build_pipeline(args) -> Pipeline:
    if args.mock_agent:
        agent = MockVisionAgent(blur_threshold=CONFIG.blur_variance_threshold)
    else:
        if not (CONFIG.azure_openai_endpoint and CONFIG.azure_openai_key and CONFIG.azure_openai_deployment):
            sys.exit(
                "Missing Azure OpenAI configuration. Set AZURE_OPENAI_ENDPOINT / "
                "AZURE_OPENAI_KEY / AZURE_OPENAI_DEPLOYMENT in .env, or pass --mock-agent."
            )
        agent = AzureVisionAgent(
            endpoint=CONFIG.azure_openai_endpoint,
            api_key=CONFIG.azure_openai_key,
            deployment=CONFIG.azure_openai_deployment,
            api_version=CONFIG.azure_openai_api_version,
        )

    if args.local_backup:
        backup_store = LocalBackupStore(CONFIG.local_backup_dir)
    else:
        if not CONFIG.azure_storage_connection_string:
            sys.exit(
                "Missing AZURE_STORAGE_CONNECTION_STRING in .env, or pass --local-backup."
            )
        backup_store = AzureBackupStore(
            connection_string=CONFIG.azure_storage_connection_string,
            container=CONFIG.azure_storage_container,
            quarantine_container=CONFIG.azure_quarantine_container,
            cosmos_endpoint=CONFIG.azure_cosmos_endpoint,
            cosmos_key=CONFIG.azure_cosmos_key,
            cosmos_database=CONFIG.azure_cosmos_database,
            cosmos_container=CONFIG.azure_cosmos_container,
        )

    return Pipeline(
        config=CONFIG,
        agent=agent,
        backup_store=backup_store,
        dry_run=args.dry_run,
        stop_file=CONFIG.stop_file_path,
    )


def main():
    parser = argparse.ArgumentParser(description="Analyze and Backup -- SD card processing pipeline")
    parser.add_argument("--mount-path", default=CONFIG.sd_card_mount_path, help="SD card mount path to scan")
    parser.add_argument("--mock-agent", action="store_true", help="Use the offline MockVisionAgent instead of Azure OpenAI")
    parser.add_argument("--local-backup", action="store_true", help="Write to local_backup/ instead of Azure Blob Storage")
    parser.add_argument("--dry-run", action="store_true", help="Log actions but don't actually delete files")
    args = parser.parse_args()

    pipeline = build_pipeline(args)
    results = pipeline.process_card(Path(args.mount_path))

    counts: dict[str, int] = {}
    for r in results:
        counts[r.action] = counts.get(r.action, 0) + 1
        print(f"[{r.action:16s}] {r.path}  -- {r.detail}")

    print("\nSummary:")
    for action, count in sorted(counts.items()):
        print(f"  {action}: {count}")


if __name__ == "__main__":
    main()
