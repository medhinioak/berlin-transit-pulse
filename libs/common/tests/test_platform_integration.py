"""Integration tests for the compose platform: the Kafka topic and the Postgres roles.

Run with `make up` and then `make test-integration`. Connection settings default to the
compose dev values and can be overridden with BTP_KAFKA_BOOTSTRAP, BTP_PG_HOST, BTP_PG_PORT
and BTP_PG_<ROLE>_PASSWORD.
"""

import os
import uuid

import psycopg
import pytest
from confluent_kafka.admin import AdminClient, ConfigResource, ResourceType
from psycopg import errors, sql
from psycopg.rows import TupleRow

pytestmark = pytest.mark.integration

ROLES = {
    "btp_processor": "PROCESSOR",
    "btp_schedule": "SCHEDULE",
    "btp_metrics_jobs": "METRICS_JOBS",
    "btp_api": "API",
}


def _connect(role: str) -> psycopg.Connection[TupleRow]:
    """Connect to the btp database as one of the service roles."""
    suffix = ROLES[role]
    return psycopg.connect(
        host=os.environ.get("BTP_PG_HOST", "localhost"),
        port=int(os.environ.get("BTP_PG_PORT", "5432")),
        dbname="btp",
        user=role,
        password=os.environ.get(f"BTP_PG_{suffix}_PASSWORD", f"dev_{suffix.lower()}"),
        connect_timeout=5,
    )


def _table(schema: str) -> sql.Composed:
    """Return a unique, fully qualified test table name in the given schema."""
    return sql.SQL("{}.{}").format(
        sql.Identifier(schema), sql.Identifier(f"it_{uuid.uuid4().hex[:12]}")
    )


def test_trip_updates_topic() -> None:
    """trip-updates has 6 partitions, 24 h retention and the delete cleanup policy."""
    admin = AdminClient(
        {"bootstrap.servers": os.environ.get("BTP_KAFKA_BOOTSTRAP", "localhost:9094")}
    )
    topics = admin.list_topics(timeout=10).topics
    assert "trip-updates" in topics
    assert len(topics["trip-updates"].partitions) == 6

    resource = ConfigResource(ResourceType.TOPIC, "trip-updates")
    configs = admin.describe_configs([resource])[resource].result(timeout=10)
    assert configs["retention.ms"].value == "86400000"
    assert configs["cleanup.policy"].value == "delete"


@pytest.mark.parametrize(
    ("role", "own_schema", "other_schema"),
    [
        ("btp_processor", "events", "schedule"),
        ("btp_schedule", "schedule", "events"),
        ("btp_metrics_jobs", "marts", "events"),
    ],
)
def test_writer_creates_only_in_own_schema(role: str, own_schema: str, other_schema: str) -> None:
    """Each writing role can create tables in its own schema and not in another."""
    with _connect(role) as conn:
        conn.execute(sql.SQL("CREATE TABLE {} (id int)").format(_table(own_schema)))
        conn.rollback()
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(sql.SQL("CREATE TABLE {} (id int)").format(_table(other_schema)))
        conn.rollback()


@pytest.mark.parametrize("role", list(ROLES))
def test_nobody_creates_in_public(role: str) -> None:
    """No service role can create objects in the public schema."""
    with _connect(role) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(sql.SQL("CREATE TABLE {} (id int)").format(_table("public")))
        conn.rollback()


def test_api_cannot_create_in_marts() -> None:
    """The API role is read-only, even in marts."""
    with _connect("btp_api") as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(sql.SQL("CREATE TABLE {} (id int)").format(_table("marts")))
        conn.rollback()


@pytest.mark.parametrize(
    ("owner", "schema", "reader"),
    [
        ("btp_processor", "events", "btp_metrics_jobs"),
        ("btp_schedule", "schedule", "btp_metrics_jobs"),
        ("btp_metrics_jobs", "marts", "btp_api"),
    ],
)
def test_reader_can_select_but_not_write_new_tables(owner: str, schema: str, reader: str) -> None:
    """Default privileges let the reader select from a table the owner creates later, not write."""
    table = _table(schema)
    with _connect(owner) as owner_conn:
        owner_conn.execute(sql.SQL("CREATE TABLE {} (id int)").format(table))
        owner_conn.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(table))
        owner_conn.commit()
        try:
            with _connect(reader) as reader_conn:
                row = reader_conn.execute(sql.SQL("SELECT id FROM {}").format(table)).fetchone()
                assert row == (1,)
                with pytest.raises(errors.InsufficientPrivilege):
                    reader_conn.execute(sql.SQL("INSERT INTO {} VALUES (2)").format(table))
                reader_conn.rollback()
        finally:
            owner_conn.execute(sql.SQL("DROP TABLE {}").format(table))
            owner_conn.commit()


@pytest.mark.parametrize("schema", ["events", "schedule"])
def test_api_cannot_read_source_schemas(schema: str) -> None:
    """The API role has no access to events or schedule."""
    with _connect("btp_api") as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(sql.SQL("SELECT 1 FROM {}").format(_table(schema)))
        conn.rollback()
