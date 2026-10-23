import os
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.procurement.config import PROCUREMENT_TEST_DATABASE_URL
from agent.procurement.database import Database


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    dsn = os.getenv(
        "PROCUREMENT_TEST_DATABASE_URL", PROCUREMENT_TEST_DATABASE_URL
    )
    try:
        admin = await asyncpg.connect(dsn)
    except Exception as exc:
        pytest.skip(f"PostgreSQL test database unavailable: {exc}")

    schema = f"test_{uuid.uuid4().hex[:12]}"
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await admin.close()

    database = Database(dsn, schema=schema)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()
        admin = await asyncpg.connect(dsn)
        try:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await admin.close()
