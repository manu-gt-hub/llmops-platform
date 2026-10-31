"""
db.py — PostgreSQL access for the gateway (async psycopg 3 pool).
"""

from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from gateway import config
from gateway.budgets import Budgets

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

pool = AsyncConnectionPool(config.POSTGRES_DSN, min_size=1, max_size=10, open=False)


async def open_pool() -> None:
    await pool.open(wait=True, timeout=60)


async def close_pool() -> None:
    await pool.close()


async def apply_schema() -> None:
    async with pool.connection() as conn:
        await conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


async def sync_budgets(budgets: Budgets) -> None:
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM budgets")
        for b in budgets.all():
            await conn.execute(
                "INSERT INTO budgets (project, monthly_usd, alert_at) VALUES (%s, %s, %s)",
                (b.project, b.monthly_usd, b.alert_at),
            )


async def record_deployment() -> bool:
    """Insert a deployment row when this version/commit was not the last one
    to start. Returns True when a new release was recorded."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT version, git_commit FROM deployments "
            "WHERE service = %s AND NOT synthetic ORDER BY ts DESC LIMIT 1",
            (config.SERVICE_NAME,),
        )
        last = await cur.fetchone()
        if last == (config.APP_VERSION, config.GIT_COMMIT):
            return False
        await conn.execute(
            "INSERT INTO deployments (service, version, git_commit, build_date, environment) "
            "VALUES (%s, %s, %s, %s, %s)",
            (config.SERVICE_NAME, config.APP_VERSION, config.GIT_COMMIT, config.BUILD_DATE, config.DEPLOY_ENV),
        )
        return True


async def insert_request(row: dict) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join(f"%({k})s" for k in row)
    async with pool.connection() as conn:
        await conn.execute(f"INSERT INTO llm_requests ({columns}) VALUES ({placeholders})", row)


async def month_spend(project: str) -> float:
    """Month-to-date real spend of a project (synthetic rows excluded)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_requests "
            "WHERE project = %s AND NOT synthetic AND ts >= date_trunc('month', now())",
            (project,),
        )
        return float((await cur.fetchone())[0])


async def run_spend(run_id: str) -> float:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_requests WHERE run_id = %s", (run_id,)
        )
        return float((await cur.fetchone())[0])


async def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    async with pool.connection() as conn:
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute(sql, params)
        return await cur.fetchall()


async def execute(sql: str, params: dict | tuple = ()) -> None:
    async with pool.connection() as conn:
        await conn.execute(sql, params)
