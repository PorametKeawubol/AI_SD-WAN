"""One-way, verified import of legacy SQLite state into an empty TimescaleDB stream."""

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sdwan.domain import Telemetry
from sdwan.storage import Store, json_value


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def migrate(source, store):
    source = Path(source).resolve(strict=True)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as legacy:
        legacy.row_factory = sqlite3.Row
        telemetry = [
            json.loads(row["payload"])
            for row in legacy.execute("SELECT payload FROM telemetry ORDER BY timestamp,link_id")
        ]
        events = [
            {**dict(row), "payload": json.loads(row["payload"])}
            for row in legacy.execute("SELECT id,timestamp,kind,payload FROM events ORDER BY id")
        ]
        state = {
            row["key"]: json.loads(row["payload"])
            for row in legacy.execute("SELECT key,payload FROM state ORDER BY key")
        }
    before = {"telemetry": telemetry, "events": events, "state": state}
    with store.connection() as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", ("sdwan-import-" + store.stream,)
        )
        for table in ("telemetry", "events", "state"):
            if connection.execute(
                "SELECT count(*) AS n FROM " + table + " WHERE stream=%s", (store.stream,)
            ).fetchone()["n"]:
                raise RuntimeError("Destination stream is not empty; existing data preserved")
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO telemetry VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    store.telemetry_record(store.stream, Telemetry.model_validate(row))
                    for row in telemetry
                ],
            )
            cursor.executemany(
                "INSERT INTO events(stream,id,observed_at,timestamp,kind,payload) VALUES (%s,%s,%s,%s,%s,%s)",
                [
                    (
                        store.stream,
                        row["id"],
                        datetime.fromtimestamp(row["timestamp"], UTC),
                        row["timestamp"],
                        row["kind"],
                        json_value(row["payload"]),
                    )
                    for row in events
                ],
            )
            cursor.executemany(
                "INSERT INTO state(stream,key,payload) VALUES (%s,%s,%s)",
                [(store.stream, key, json_value(value)) for key, value in state.items()],
            )
        connection.execute(
            "SELECT setval(pg_get_serial_sequence('events','id'), COALESCE((SELECT max(id) FROM events),1), EXISTS(SELECT 1 FROM events))"
        )
    snapshot = store.export()
    after = {key: snapshot[key] for key in ("telemetry", "events", "state")}
    if digest(before) != digest(after):
        raise RuntimeError("Import verification mismatch; original SQLite source preserved")
    return {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "destination": "timescaledb",
        "stream": store.stream,
        "counts": {"telemetry": len(telemetry), "events": len(events), "state": len(state)},
        "source_content_sha256": digest(before),
        "destination_content_sha256": digest(after),
        "content_verified": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--stream", default="live")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError("Previous migration evidence preserved")
    result = migrate(args.source, Store(stream=args.stream))
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
