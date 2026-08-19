import json
from pathlib import Path

import pytest

from analyze_and_backup import pipeline as pipeline_module
from analyze_and_backup.agent import AgentResult
from analyze_and_backup.config import Config
from analyze_and_backup.dedup import DuplicateIndex
from analyze_and_backup.metadata import PhotoMetadata
from analyze_and_backup.pipeline import Pipeline
from analyze_and_backup.storage import LocalBackupStore


class StubAgent:
    """Returns a fixed AgentResult regardless of input; counts calls so tests
    can assert the agent was (or wasn't) invoked, e.g. for the dedup fast-path."""

    def __init__(self, result: AgentResult):
        self.result = result
        self.calls = 0

    def classify(self, image_path, local_scores):
        self.calls += 1
        return self.result


@pytest.fixture(autouse=True)
def stub_exiftool(monkeypatch):
    """extract_metadata normally shells out to the real exiftool binary, which
    isn't installed in this dev/test environment. Pipeline tests care about
    routing logic, not exiftool parsing (that's covered by test_metadata.py),
    so stub it out with deterministic fake metadata."""

    def fake_extract_metadata(path, exiftool_binary="exiftool"):
        return PhotoMetadata(
            path=str(path),
            timestamp="2026:08:12 14:30:00",
            camera_model="Test Camera",
            gps_latitude=None,
            gps_longitude=None,
            width=100,
            height=100,
            file_sha256="0" * 64,
            raw={},
        )

    monkeypatch.setattr(pipeline_module, "extract_metadata", fake_extract_metadata)


def _build_pipeline(agent, tmp_path):
    config = Config()
    backup_store = LocalBackupStore(tmp_path / "local_backup")
    pipe = Pipeline(config=config, agent=agent, backup_store=backup_store, dedup_index=DuplicateIndex(threshold=8))
    return pipe, backup_store


def _index_records(backup_store):
    lines = backup_store.index_path.read_text().splitlines()
    return [json.loads(line) for line in lines]


def test_confident_bad_photo_is_quarantined(make_noise_image, tmp_path):
    photo = Path(make_noise_image(seed=0, name="bad.png"))
    agent = StubAgent(AgentResult("bad", confidence=0.9, quality_score=0.1,
                                   extracted_text=None, category=None, reasoning="too blurry"))
    pipe, store = _build_pipeline(agent, tmp_path)

    result = pipe.process_photo(photo)

    assert result.action == "quarantined"
    assert not photo.exists()  # removed from the "SD card" ...
    quarantined_copy = tmp_path / "local_backup" / "quarantine" / "bad.png"
    assert quarantined_copy.exists()  # ... but safely copied here first, not lost
    records = _index_records(store)
    assert records[-1]["kind"] == "quarantine"
    assert records[-1]["metadata"]["reason"] == "too blurry"
    assert records[-1]["metadata"]["batch_id"] == pipe.batch_id


def test_low_confidence_bad_photo_is_kept_uncertain(make_noise_image, tmp_path):
    photo = Path(make_noise_image(seed=0, name="maybe_bad.png"))
    agent = StubAgent(AgentResult("bad", confidence=0.5, quality_score=0.3,
                                   extracted_text=None, category=None, reasoning="might be blurry"))
    pipe, store = _build_pipeline(agent, tmp_path)

    result = pipe.process_photo(photo)

    assert result.action == "kept_uncertain"
    assert photo.exists()  # never deleted -- confidence below floor
    records = _index_records(store)
    assert records[-1]["kind"] == "uncertain"


def test_document_photo_is_stored_as_document(make_noise_image, tmp_path):
    photo = Path(make_noise_image(seed=0, name="receipt.png"))
    agent = StubAgent(AgentResult("document", confidence=0.95, quality_score=0.8,
                                   extracted_text="Total: $12.34", category="receipt",
                                   reasoning="looks like a receipt"))
    pipe, store = _build_pipeline(agent, tmp_path)

    result = pipe.process_photo(photo)

    assert result.action == "stored_document"
    records = _index_records(store)
    assert records[-1]["kind"] == "document"
    assert records[-1]["metadata"]["extracted_text"] == "Total: $12.34"
    assert records[-1]["metadata"]["category"] == "receipt"
    assert (tmp_path / "local_backup" / "documents" / "receipt.png").exists()


def test_good_photo_is_backed_up(make_noise_image, tmp_path):
    photo = Path(make_noise_image(seed=0, name="good.png"))
    agent = StubAgent(AgentResult("photo", confidence=0.95, quality_score=0.9,
                                   extracted_text=None, category=None, reasoning="looks fine"))
    pipe, store = _build_pipeline(agent, tmp_path)

    result = pipe.process_photo(photo)

    assert result.action == "stored_photo"
    records = _index_records(store)
    assert records[-1]["kind"] == "photo"
    assert (tmp_path / "local_backup" / "photos" / "good.png").exists()


def test_duplicate_is_deleted_without_calling_agent(make_noise_image, tmp_path):
    agent = StubAgent(AgentResult("photo", confidence=0.95, quality_score=0.9,
                                   extracted_text=None, category=None, reasoning="looks fine"))
    pipe, store = _build_pipeline(agent, tmp_path)

    first = Path(make_noise_image(seed=0, name="first.png"))
    second = Path(make_noise_image(seed=0, name="second.png"))  # identical pixels

    result1 = pipe.process_photo(first)
    result2 = pipe.process_photo(second)

    assert result1.action == "stored_photo"
    assert result2.action == "deleted_duplicate"
    assert not second.exists()  # removed from the "SD card" ...
    duplicate_copy = tmp_path / "local_backup" / "duplicates" / "second.png"
    assert duplicate_copy.exists()  # ... but a copy is kept here so it's still visible
    assert agent.calls == 1  # agent never ran on the duplicate
    records = _index_records(store)
    assert records[-1]["kind"] == "duplicate"


def test_agent_duplicate_classification_still_auto_deletes(make_noise_image, tmp_path):
    """The agent is instructed never to return "duplicate" (real dedup
    happens earlier via perceptual hashing), but if it ever did, this is a
    defensive fallback -- duplicates keep auto-deleting immediately per the
    team's decision, unlike "bad" which now goes to quarantine instead."""
    photo = Path(make_noise_image(seed=0, name="weird_duplicate.png"))
    agent = StubAgent(AgentResult("duplicate", confidence=0.9, quality_score=0.5,
                                   extracted_text=None, category=None, reasoning="agent thinks duplicate"))
    pipe, store = _build_pipeline(agent, tmp_path)

    result = pipe.process_photo(photo)

    assert result.action == "deleted_duplicate"
    assert not photo.exists()
    assert not (tmp_path / "local_backup" / "quarantine" / "weird_duplicate.png").exists()
    assert (tmp_path / "local_backup" / "duplicates" / "weird_duplicate.png").exists()
    records = _index_records(store)
    assert records[-1]["kind"] == "duplicate"


def _build_pipeline_with_stop_file(agent, tmp_path, stop_file):
    config = Config()
    backup_store = LocalBackupStore(tmp_path / "local_backup")
    pipe = Pipeline(
        config=config, agent=agent, backup_store=backup_store,
        dedup_index=DuplicateIndex(threshold=8), stop_file=stop_file,
    )
    return pipe, backup_store


class StopAfterNAgent:
    """Like StubAgent, but touches the stop file partway through -- simulates
    someone pressing the web UI's Stop button while a run is in progress."""

    def __init__(self, result: AgentResult, stop_file: Path, n: int):
        self.result = result
        self.stop_file = stop_file
        self.n = n
        self.calls = 0

    def classify(self, image_path, local_scores):
        self.calls += 1
        if self.calls == self.n:
            self.stop_file.touch()
        return self.result


def test_process_card_halts_when_stop_requested_mid_run(make_noise_image, tmp_path):
    """A stale stop file from a previous run is discarded at the start of
    process_card (see clear_stop_request() call before the loop) -- so this
    test simulates the realistic case: the button is pressed WHILE a run is
    already in progress, and process_card should stop before the next photo
    rather than blowing through the rest of the card."""
    mount = tmp_path / "card"
    mount.mkdir()
    make_noise_image(seed=0, name="card/one.png")
    make_noise_image(seed=1, name="card/two.png")
    make_noise_image(seed=2, name="card/three.png")
    # sorted() order is one.png, three.png, two.png

    stop_file = tmp_path / "STOP_REQUESTED"
    result = AgentResult("photo", confidence=0.95, quality_score=0.9,
                          extracted_text=None, category=None, reasoning="looks fine")
    agent = StopAfterNAgent(result, stop_file, n=1)  # touch stop file right after photo #1
    pipe, store = _build_pipeline_with_stop_file(agent, tmp_path, stop_file)

    results = pipe.process_card(mount)

    assert len(results) == 1  # only one.png -- halted before three.png/two.png
    assert results[0].path.endswith("one.png")
    assert agent.calls == 1
    assert not stop_file.exists()  # cleared for the next run


def test_process_card_clears_stop_file_after_completing(make_noise_image, tmp_path):
    agent = StubAgent(AgentResult("photo", confidence=0.95, quality_score=0.9,
                                   extracted_text=None, category=None, reasoning="looks fine"))
    mount = tmp_path / "card"
    mount.mkdir()
    make_noise_image(seed=0, name="card/one.png")

    stop_file = tmp_path / "STOP_REQUESTED"
    pipe, store = _build_pipeline_with_stop_file(agent, tmp_path, stop_file)

    results = pipe.process_card(mount)

    assert len(results) == 1
    assert not stop_file.exists()


def test_stop_requested_and_clear_stop_request(tmp_path):
    agent = StubAgent(AgentResult("photo", confidence=0.95, quality_score=0.9,
                                   extracted_text=None, category=None, reasoning="looks fine"))
    stop_file = tmp_path / "STOP_REQUESTED"
    pipe, _ = _build_pipeline_with_stop_file(agent, tmp_path, stop_file)

    assert pipe.stop_requested() is False

    stop_file.touch()
    assert pipe.stop_requested() is True

    pipe.clear_stop_request()
    assert pipe.stop_requested() is False
    assert not stop_file.exists()


def test_pipeline_without_stop_file_never_halts(make_noise_image, tmp_path):
    """No stop_file passed at all (e.g. plain CLI usage without the web
    interface) -- stop_requested() must always be False, never crash on
    a None path."""
    agent = StubAgent(AgentResult("photo", confidence=0.95, quality_score=0.9,
                                   extracted_text=None, category=None, reasoning="looks fine"))
    pipe, store = _build_pipeline(agent, tmp_path)

    assert pipe.stop_requested() is False
    pipe.clear_stop_request()  # should be a no-op, not raise

    mount = tmp_path / "card"
    mount.mkdir()
    make_noise_image(seed=0, name="card/one.png")
    results = pipe.process_card(mount)
    assert len(results) == 1
