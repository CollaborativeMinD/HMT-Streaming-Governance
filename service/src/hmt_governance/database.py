"""Bounded async connection pool; PostgreSQL is the only authoritative store."""

from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .contracts import Rejected


class Database:
    def __init__(self, settings):
        self.settings = settings
        self.last_error = None
        self.pool = AsyncConnectionPool(
            settings.dsn,
            min_size=1,
            max_size=settings.workers + 2,
            max_waiting=settings.max_jobs,
            timeout=2,
            open=False,
            kwargs=dict(
                row_factory=dict_row,
                options="-c statement_timeout=2500 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=5000",
            ),
        )

    async def start(self):
        try:
            await self.pool.open(wait=True, timeout=5)
            async with self.connection() as connection:
                result = await (
                    await connection.execute("SELECT version FROM governance.meta")
                ).fetchall()
                if result != [dict(version=1)]:
                    raise Rejected("SCHEMA_INCOMPATIBLE", "HELD")
                await self.check_schema(connection)
            return self
        except BaseException:
            await self.pool.close(timeout=5)
            raise

    async def check_schema(self, connection):
        import re

        source = Path(__file__).with_name("schema.sql").read_text()
        expected = dict(re.findall(r"CREATE FUNCTION governance\.(\w+)\(.*?AS\s+\$\$(.*?)\$\$;", source, re.S))
        rows = await (await connection.execute(
            "SELECT p.proname,p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='governance'"
        )).fetchall()
        actual = {r['proname']:r['prosrc'] for r in rows}
        if actual != expected:
            raise Rejected("SCHEMA_FUNCTION_DRIFT", "HELD")
        required = set(re.findall(r"CREATE TRIGGER (\w+)",source))
        rows = await (await connection.execute(
            "SELECT t.tgname,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='governance' AND NOT t.tgisinternal"
        )).fetchall()
        if {r['tgname'] for r in rows if r['tgenabled']=='O'} != required:
            raise Rejected("SCHEMA_TRIGGER_DRIFT", "HELD")

    async def close(self):
        await self.pool.close(timeout=5)

    @asynccontextmanager
    async def connection(self):
        try:
            async with self.pool.connection() as connection:
                yield connection
        except psycopg.Error as exc:
            self.last_error = type(exc).__name__ + ":" + str(exc.sqlstate)
            raise Rejected("POSTGRES_UNAVAILABLE", "HELD") from None


async def migrate(dsn):
    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True
    ) as connection:
        async with connection.transaction():
            await connection.execute(Path(__file__).with_name("schema.sql").read_text())
