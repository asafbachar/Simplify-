"""Fixtures for the exercise. Nothing here needs changing, but read it.

    db        -> a psycopg connection to the ingest database
    replay    -> run a sequence of batches, optionally resetting first
    client    -> httpx client against the serving API
    raw_records -> the raw NDJSON as parsed dicts, straight off disk
"""
import json
import subprocess
from pathlib import Path

import httpx
import psycopg
import pytest

def _repo_root() -> Path:
    """Walk up until we find bin/pipeline, so this file works wherever it sits."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "bin" / "pipeline").exists():
            return parent
    raise RuntimeError("cannot locate repo root: no bin/pipeline above this file")


ROOT = _repo_root()
PIPELINE = ROOT / "bin" / "pipeline"
RAW = ROOT / "raw"
DSN = "postgresql://qa:qa@localhost:5433/ingest"
API = "http://localhost:8080"


@pytest.fixture
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        yield conn


@pytest.fixture
def replay():
    """replay("B01", "B02")            -> reset, then run B01 then B02
       replay("B03", reset=False)      -> run B03 on top of current state

    Batch ids may repeat: replay("B04", "B04") runs B04 twice.
    """
    def _replay(*batch_ids, reset=True):
        if reset:
            subprocess.run([str(PIPELINE), "reset"], check=True,
                           capture_output=True, text=True)
        for bid in batch_ids:
            subprocess.run([str(PIPELINE), "run", "--batch", bid], check=True,
                           capture_output=True, text=True)
    return _replay


@pytest.fixture
def client():
    with httpx.Client(base_url=API, timeout=30) as c:
        yield c


@pytest.fixture(scope="session")
def raw_records():
    """{"B01": [ {...}, ... ], ...} — the batches as delivered, unprocessed."""
    out = {}
    for path in sorted(RAW.glob("batch_*.ndjson")):
        bid = path.stem.replace("batch_", "")
        out[bid] = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return out


@pytest.fixture(scope="session")
def all_batch_ids(raw_records):
    return sorted(raw_records)
