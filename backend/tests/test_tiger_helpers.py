"""Pure helpers in app/db/tiger.py: SQL splitting and the numpy → binary COPY encoder."""

import math
import struct

import numpy as np

from app.db import tiger


def test_split_sql_respects_strings_comments_and_dollar_quotes() -> None:
    script = """
    -- a comment; with a semicolon
    CREATE TABLE a (x text DEFAULT 'semi;colon');
    /* block; comment */
    CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $$
    BEGIN
        EXECUTE format($sql$ SELECT 1; $sql$);
        RETURN 1;
    END;
    $$;
    SELECT "weird;name" FROM a;
    SELECT 'it''s; fine'
    """
    stmts = tiger.split_sql(script)
    assert len(stmts) == 4
    assert stmts[0].startswith("CREATE TABLE a") and "'semi;colon'" in stmts[0]
    assert stmts[1].startswith("CREATE FUNCTION") and stmts[1].endswith("$$")
    assert "$sql$ SELECT 1; $sql$" in stmts[1]
    assert stmts[3] == "SELECT 'it''s; fine'"


def test_migration_files_split_into_statements() -> None:
    files = {p.name: tiger.split_sql(p.read_text()) for p in tiger.migration_files()}
    assert list(files) == ["001_schema.sql", "002_caggs.sql", "003_policies.sql", "004_jobs.sql"]
    assert len(files["002_caggs.sql"]) == 3
    assert len(files["004_jobs.sql"]) == 4  # function, job procedure, add_job block, re-enable paused jobs
    assert all(s.strip() for stmts in files.values() for s in stmts)


def _reference_copy(columns, rows) -> bytes:
    """Straightforward row-by-row PostgreSQL binary COPY encoder to check the vectorized one."""
    fmt = {"timestamptz": ">q", "int4": ">i", "int2": ">h", "float4": ">f", "bool": ">B"}
    out = [tiger.COPY_HEADER]
    for row in rows:
        out.append(struct.pack(">h", len(columns)))
        for (_, pg_type), value in zip(columns, row, strict=True):
            if value is None:
                out.append(struct.pack(">i", -1))
                continue
            if pg_type == "timestamptz":
                value -= tiger.PG_EPOCH_US
            data = struct.pack(fmt[pg_type], value)
            out.append(struct.pack(">i", len(data)) + data)
    out.append(tiger.COPY_TRAILER)
    return b"".join(out)


def test_encode_copy_binary_matches_reference_per_null_pattern() -> None:
    columns = (("ts", "timestamptz"), ("rid", "int4"), ("hr", "float4"), ("worn", "bool"), ("src", "int2"))
    ts = np.array([1_700_000_000_000_000 + i * 5_000_000 for i in range(5)], dtype=np.int64)
    data = {
        "ts": ts,
        "rid": np.array([1, 1, 2, 2, 3], dtype=np.int32),
        "hr": np.array([60.0, np.nan, 61.5, np.nan, 70.25], dtype=np.float32),
        "worn": np.array([1, 0, -1, 1, -1], dtype=np.int8),
        "src": np.zeros(5, dtype=np.int16),
    }
    payload = tiger.encode_copy_binary(columns, data)

    def row(i):
        hr = None if math.isnan(data["hr"][i]) else float(data["hr"][i])
        worn = None if data["worn"][i] < 0 else int(data["worn"][i])
        return (int(ts[i]), int(data["rid"][i]), hr, worn, 0)

    # Rows are grouped by null pattern (sorted by the pattern bitmask), in time order within a group.
    masks = [(0 if not math.isnan(data["hr"][i]) else 4) | (0 if data["worn"][i] >= 0 else 8) for i in range(5)]
    order = sorted(range(5), key=lambda i: (masks[i], i))
    assert payload == _reference_copy(columns, [row(i) for i in order])


def test_encode_copy_binary_vitals_layout_sizes() -> None:
    n = 3
    cols = {name: np.zeros(n, dtype=np.float32) for name, _ in tiger.VITALS_COLUMNS}
    cols.update(
        ts=np.arange(n, dtype=np.int64) * 5_000_000,
        resident_id=np.ones(n, dtype=np.int32),
        worn=np.ones(n, dtype=np.int8),
        in_bed=np.zeros(n, dtype=np.int8),
        source=np.zeros(n, dtype=np.int16),
    )
    payload = tiger.encode_copy_binary(tiger.VITALS_COLUMNS, cols)
    # header 19 + per row (field count 2 + 13 lengths×4 + ts 8 + int4 4 + 8 floats×4 + 2 bools + int2 2) + trailer 2
    assert len(payload) == 19 + n * (2 + 13 * 4 + 8 + 4 + 8 * 4 + 2 + 2) + 2
