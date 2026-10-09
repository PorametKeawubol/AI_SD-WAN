"""Integration checks against the owned TimescaleDB; every test owns a unique stream."""

import json
import os
import sqlite3
import uuid

import pytest

from scripts.migrate_telemetry import migrate
from sdwan.domain import Telemetry
from sdwan.storage import StorageError, Store


@pytest.fixture
def sql_store():
    if not os.getenv("SDWAN_DATABASE_URL"):
        pytest.skip("Integration tests require SDWAN_DATABASE_URL for the owned TimescaleDB")
    store = Store(stream="test-" + uuid.uuid4().hex)
    try:
        yield store
    finally:
        with store.connection() as connection:
            for table in ("telemetry", "events", "state"):
                connection.execute("DELETE FROM " + table + " WHERE stream=%s", (store.stream,))


def sample(timestamp=1000.13, link="wan1"):
    return Telemetry(
        link_id=link,
        timestamp=timestamp,
        up=True,
        latency_ms=20,
        jitter_ms=3,
        loss_pct=0,
        utilization_pct=5,
    )


def test_real_hypertable_and_encrypted_database_connection(sql_store):
    with sql_store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM timescaledb_information.hypertables WHERE hypertable_name='telemetry'"
            ).fetchone()["n"]
            == 1
        )
        assert connection.execute(
            "SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
        ).fetchone()["ssl"]


def test_raw_timestamps_history_order_and_durable_state(sql_store):
    sql_store.ingest([sample(1000.13), sample(1005.91)])
    sql_store.put("mode", "manual")
    restarted = Store(stream=sql_store.stream)
    assert [row["timestamp"] for row in restarted.history("wan1")] == [1000.13, 1005.91]
    assert restarted.history("wan1", 1)[0]["timestamp"] == 1005.91
    assert restarted.get("mode") == "manual"


def test_duplicate_batch_rolls_back_all_new_rows(sql_store):
    sql_store.ingest([sample()])
    with pytest.raises(StorageError, match="UniqueViolation"):
        sql_store.ingest([sample(1005, "wan2"), sample()])
    assert not sql_store.history("wan2")
    assert len(sql_store.history("wan1")) == 1


def test_streams_isolate_history_and_decisions(sql_store):
    sql_store.ingest([sample()])
    sql_store.log(1000, "decision", {"target": "wan2"})
    other = Store(stream=sql_store.stream + "-other")
    assert not other.history("wan1")
    assert not other.events()
    assert other.get("mode") is None


def test_sql_parameterization_and_lossless_json_audit(sql_store):
    key = "'; DROP TABLE telemetry; --"
    value = {"message": "เส้นทาง WAN2", "nested": {"flag": True, "value": None}}
    sql_store.put(key, value)
    sql_store.log(1001, "approved", value)
    assert sql_store.get(key) == value
    assert sql_store.events()[0]["payload"] == value
    assert sql_store.export()["state"][key] == value


def legacy_fixture(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE telemetry(link_id TEXT,timestamp REAL,payload TEXT); CREATE TABLE events(id INTEGER,timestamp REAL,kind TEXT,payload TEXT); CREATE TABLE state(key TEXT,payload TEXT);"
        )
        connection.execute(
            "INSERT INTO telemetry VALUES (?,?,?)", ("wan1", 1000.13, sample().model_dump_json())
        )
        connection.execute(
            "INSERT INTO events VALUES (?,?,?,?)",
            (7, 1001.0, "approved", json.dumps({"target": "wan2"})),
        )
        connection.execute("INSERT INTO state VALUES (?,?)", ("mode", json.dumps("manual")))
    return path


def test_legacy_import_verifies_full_content_and_keeps_original(sql_store, tmp_path):
    source = legacy_fixture(tmp_path / "old.sqlite3")
    original = source.read_bytes()
    result = migrate(source, sql_store)
    assert result["content_verified"]
    assert result["source_content_sha256"] == result["destination_content_sha256"]
    assert result["counts"] == {"telemetry": 1, "events": 1, "state": 1}
    assert sql_store.events()[0]["id"] == 7
    assert source.read_bytes() == original


def test_legacy_import_refuses_to_overwrite_existing_stream(sql_store, tmp_path):
    source = legacy_fixture(tmp_path / "old.sqlite3")
    sql_store.log(1001, "existing", {})
    with pytest.raises(RuntimeError, match="not empty"):
        migrate(source, sql_store)
    assert len(sql_store.events()) == 1
    assert not sql_store.history("wan1")


def test_no_sqlite_fallback_and_no_password_in_public_errors(monkeypatch):
    monkeypatch.delenv("SDWAN_DATABASE_URL", raising=False)
    with pytest.raises(StorageError, match="SDWAN_DATABASE_URL"):
        Store("data/lab.sqlite3")
    with pytest.raises(StorageError) as failure:
        Store("postgresql://user:secret-never-publish@localhost:1/db")
    assert "secret-never-publish" not in str(failure.value)
