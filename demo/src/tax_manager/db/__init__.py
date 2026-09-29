"""PostgreSQL connection boundary."""

from contextlib import contextmanager

import psycopg
from flask import current_app
from psycopg.rows import dict_row


@contextmanager
def connect():
    with psycopg.connect(
        current_app.config["DATABASE_URL"],
        connect_timeout=5,
        row_factory=dict_row,
    ) as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        yield conn
