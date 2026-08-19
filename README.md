# Analyze and Backup — edge pipeline

Raspberry Pi-side implementation: SD card is inserted, exiftool pulls metadata,
a local dedup/quality pass runs, an AI vision agent classifies each photo, and
the pipeline routes it to delete / document storage / photo backup. This
covers the edge/IoT + AI agent + testing part of the project (Azure
infrastructure, orchestration, and the dashboard are the teammate's side).

See `agent_workflow_diagram.svg`/`.png` for the visual version of this flow.

## How it works

1. **Trigger** — a new photo file appears on the SD card mount.
2. **Extract metadata** (`analyze_and_backup/metadata.py`) — shells out to the
   `exiftool` CLI for timestamp, camera model, GPS, dimensions; also computes
   a SHA-256 of the file bytes.
3. **Duplicate check** (`dedup.py`) — a 64-bit perceptual hash (`imagehash.phash`)
   is compared against every photo already seen this session (Hamming
   distance ≤ 8 = duplicate). This runs entirely on the Pi, before any cloud
   call, and resolves which copy to keep using a cheap local blur score — no
   need to invoke the AI agent on a photo that's about to be deleted anyway.
4. **Local quality checks** (`quality.py`) — Laplacian-variance blur score and
   exposure clipping/mean luminance, also local, also free.
5. **AI vision agent** (`agent.py`) — Azure OpenAI (`AzureVisionAgent`) or an
   offline rule-based stand-in (`MockVisionAgent`) classifies the photo as
   `bad`, `document`, or `photo`, with a confidence score and (for documents)
   OCR'd text + a category label.
6. **Routing + confidence floor** (`pipeline.py`) — `AGENT_CONFIDENCE_FLOOR`
   (default 0.75) is the safety net: the agent only auto-acts when
   confident. Anything below the floor is kept and logged as "uncertain"
   instead. Photos classified `bad` (blurry/dark/etc.) are **quarantined**,
   not deleted outright — copied to quarantine storage (local
   `local_backup/quarantine/` or the Azure `quarantine` blob container) and
   removed from the source only once safely copied elsewhere, so they're
   still visible for review. True duplicates (resolved locally via
   perceptual hash, before the agent ever runs) still auto-delete
   immediately, since a sharper copy is always kept — nothing unique is
   ever lost there. A copy of the removed duplicate is kept too (local
   `local_backup/duplicates/` or the Azure `duplicates` blob container),
   purely for visibility in the web interface — there's no approve/restore
   step for these the way there is for quarantine, since the sharper copy
   was always retained.
7. **Backup** (`storage.py`) — approved photos, documents, quarantined
   photos, and removed duplicates all go to Azure Blob Storage (separate
   `photos`/`documents`/`quarantine`/`duplicates` containers), indexed in
   Cosmos DB using the same
   database/container the teammate's `api/` dashboard code already queries
   (`id`/`filename`/`batch_id`/`status`/`type` fields — falls back to a
   local JSON-lines log if Cosmos credentials aren't configured).
8. **Web interface** (`webapp.py`) — a local Flask app that reads straight
   from `local_backup/` and shows what happened: a photo gallery, a
   document search (by keyword or date), and an activity log showing
   quarantined photos (with thumbnails), duplicates removed, and anything
   flagged uncertain. A printed QR code (`generate_qr.py`) points at this
   page so someone who just inserted a card can scan it on their phone and
   see the results without touching the Pi. See `deploy/README.md` for
   wiring this up to run automatically on card insert.

## Setup

```bash
cd analyze-and-backup
pip install -r requirements.txt
cp .env.example .env        # fill in Azure OpenAI + Storage credentials
sudo apt install libimage-exiftool-perl   # on the actual Raspberry Pi
```

## Running

```bash
# Real run: Azure OpenAI + Azure Blob Storage
python cli.py --mount-path /media/pi/SD_CARD

# Fully offline dry run -- no Azure account needed, nothing actually deleted
python cli.py --mount-path ./sample_photos --mock-agent --local-backup --dry-run
```

`sample_photos/` and `local_backup/` in this folder are leftovers from a demo
run (mock agent, four synthetic test images) — safe to delete on your own
machine; they're just here to show what the output looks like. `index.jsonl`
inside `local_backup/` is the audit log every action gets written to.

## Testing

```bash
pytest -v
```

32 tests, all passing: perceptual-hash duplicate matching, blur/exposure
scoring, exiftool JSON parsing (mocked, since exiftool itself isn't needed to
run the test suite), full pipeline routing (confident delete, low-confidence
"keep as uncertain", document storage, photo backup, duplicate fast-path that
skips the agent entirely), stop-mid-run handling, and document search/filtering.

## Thresholds — what's real vs. placeholder

`config.py` documents this per-value, but the short version: the duplicate
Hamming distance (8) and confidence floor (0.75) are reasonable defaults from
general practice. **The blur variance threshold (100.0) is a placeholder and
needs to be recalibrated against real photos from the actual Pi camera** —
shoot 30-50 sample photos including some deliberately blurry ones, run
`blur_score()` on each, and pick the threshold at the gap between the sharp
and blurry clusters. Same idea applies to exposure thresholds if the camera's
sensor behaves differently than assumed.

## Known simplification worth flagging in your writeup

The proposal frames the agent under a **Recommend-Approve-Act** pattern.
This implementation now honors that pattern for `bad` classifications —
they're quarantined (recommend) rather than deleted, and only actually
removed once approved via the teammate's `api/quarantine.py` endpoints (act)
— but **not** for duplicates: those still auto-delete immediately with no
approve step, since only the sharper copy is ever discarded and nothing
unique is at risk. Worth stating that distinction explicitly in your
individual documentation rather than leaving it implicit.
