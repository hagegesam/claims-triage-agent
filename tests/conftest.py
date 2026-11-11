import pytest

from claims_agent import dataset


@pytest.fixture
def data(tmp_path, monkeypatch):
    """A fresh copy of the generated dataset; the core system points at it."""
    dataset.write(tmp_path)
    monkeypatch.setenv("CLAIMS_DATA_DIR", str(tmp_path))
    return {c.claim_id: c for c in dataset.build_cases()}


def first(data, scenario):
    return next(c for c in data.values() if c.scenario == scenario)
