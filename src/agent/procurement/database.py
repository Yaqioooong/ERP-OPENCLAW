"""PostgreSQL connection pool for procurement business facts."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg


class Database:
    def __init__(self, dsn: str, *, schema: str | None = None):
        self._dsn = dsn
        self._schema = schema
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is not None:
            return
        server_settings = (
            {"search_path": self._schema} if self._schema else None
        )
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=1,
            max_size=10,
            server_settings=server_settings,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        await self.connect()
        assert self._pool is not None
        async with self._pool.acquire() as connection:
            yield connection
