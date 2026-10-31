"""
main.py — LLM gateway: provider proxy + AgentOps API + build info.

Run with:  uvicorn gateway.main:app --host 0.0.0.0 --port 8080
"""

import asyncio
import html
import logging
import posixpath
import re
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

import markdown
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from opentelemetry import trace
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from gateway import config, db, governance, seed
from gateway.budgets import Budgets
from gateway.console import router as console_router
from gateway.pricing import PriceList
from gateway.proxy import Attribution, Proxy
from gateway.runs import router as runs_router
from gateway.telemetry import setup_tracing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gateway")

DOC_DIR = config.PROJECT_ROOT / "doc"


async def _wait_for_db(attempts: int = 30) -> None:
    for attempt in range(1, attempts + 1):
        try:
            await db.open_pool()
            return
        except Exception as exc:  # database still starting
            log.info("waiting for PostgreSQL (%s/%s): %s", attempt, attempts, exc)
            await asyncio.sleep(2)
    raise RuntimeError("PostgreSQL is not reachable")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _wait_for_db()
    await db.apply_schema()
    budgets = Budgets.load(config.BUDGETS_FILE)
    await db.sync_budgets(budgets)
    await governance.sync_registry(governance.load_registry(config.AGENTS_FILE))
    if await db.record_deployment():
        log.info("new release recorded: %s (%s)", config.APP_VERSION, config.GIT_COMMIT)
    if config.SEED_DEMO_DATA:
        await seed.seed_if_empty(config.SEED_DAYS)

    app.state.proxy = Proxy(PriceList.load(config.PRICING_FILE), budgets, tracer)
    log.info("gateway %s ready (anthropic=%s, openai=%s)", config.APP_VERSION,
             bool(config.ANTHROPIC_API_KEY), bool(config.OPENAI_API_KEY))
    yield
    await app.state.proxy.aclose()
    await db.close_pool()


tracer = setup_tracing(config.SERVICE_NAME, config.APP_VERSION, config.OTEL_EXPORTER_OTLP_ENDPOINT)

# FastAPI's built-in OpenTelemetry creates the HTTP server span of every
# request (continuing the caller's traceparent); probes are left out.
app = FastAPI(
    title="LLMOps Gateway",
    version=config.APP_VERSION,
    lifespan=lifespan,
    telemetry={
        "tracer_provider": trace.get_tracer_provider(),
        "operation_spans": False,
        "exclude": lambda scope: scope.get("path") in ("/health", "/metrics"),
    },
)
app.include_router(runs_router)
app.include_router(console_router)


# ---------------------------------------------------------------------------
# Ops endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"])
async def health() -> dict:
    await db.fetch_all("SELECT 1")
    return {"status": "ok"}


@app.get("/version", tags=["ops"])
async def version() -> dict:
    return {
        "service": config.SERVICE_NAME,
        "version": config.APP_VERSION,
        "commit": config.GIT_COMMIT,
        "build_date": config.BUILD_DATE,
        "environment": config.DEPLOY_ENV,
        "providers": {"anthropic": bool(config.ANTHROPIC_API_KEY), "openai": bool(config.OPENAI_API_KEY)},
    }


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/summary", tags=["finops"])
async def summary() -> dict:
    """Quick FinOps snapshot (the dashboards have the full picture)."""
    rows = await db.fetch_all(
        """
        SELECT project,
               SUM(cost_usd) FILTER (WHERE ts >= date_trunc('month', now())) AS cost_mtd,
               SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens)
                   FILTER (WHERE ts >= date_trunc('day', now())) AS tokens_today,
               COUNT(*) FILTER (WHERE ts >= now() - interval '24 hours') AS requests_24h
        FROM llm_requests WHERE NOT synthetic GROUP BY project ORDER BY project
        """
    )
    return {"projects": [
        {k: (float(v) if k == "cost_mtd" and v is not None else v) for k, v in r.items()} for r in rows
    ]}


# ---------------------------------------------------------------------------
# Architecture docs rendered in the browser (doc/*.md, Mermaid diagrams)
# ---------------------------------------------------------------------------
_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;line-height:1.55;color:#1f2328}}
pre{{background:#f6f8fa;padding:1rem;overflow-x:auto}} code{{font-size:.9em}}
table{{border-collapse:collapse}} td,th{{border:1px solid #d0d7de;padding:.3rem .6rem}}
pre.mermaid{{background:none}} a{{color:#0969da}}
</style></head><body>{body}
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs";
mermaid.initialize({{startOnLoad: true}});
</script></body></html>"""

_MERMAID = re.compile(r'<pre><code class="language-mermaid">(.*?)</code></pre>', re.S)
_RELATIVE_HREF = re.compile(r'href="(?!https?://|#|/)([^"#]+)(#[^"]*)?"')


def _link(doc_dir: PurePosixPath, href: str, anchor: str | None) -> str:
    """Docs link to each other (.md) and to code; serve docs from this route
    and send code links to the repository."""
    target = PurePosixPath(posixpath.normpath(doc_dir / href))
    if target.suffix == ".md" and not str(target).startswith(".."):
        return f'href="/architecture/{target.with_suffix("")}{anchor or ""}"'
    return f'href="{config.REPO_URL}/blob/main/{posixpath.normpath(PurePosixPath("doc") / target)}{anchor or ""}"'


def _render(path) -> str:
    body = markdown.markdown(path.read_text(encoding="utf-8"), extensions=["fenced_code", "tables"])
    body = _MERMAID.sub(lambda m: f'<pre class="mermaid">{m.group(1)}</pre>', body)
    doc_dir = PurePosixPath(path.parent.relative_to(DOC_DIR).as_posix())
    body = _RELATIVE_HREF.sub(lambda m: _link(doc_dir, m.group(1), m.group(2)), body)
    return _PAGE.format(title=html.escape(path.stem), body=body)


@app.get("/architecture", response_class=HTMLResponse, include_in_schema=False)
@app.get("/architecture/{name:path}", response_class=HTMLResponse, include_in_schema=False)
async def architecture(name: str = "architecture") -> str:
    path = (DOC_DIR / f"{name.removeprefix('doc/')}.md").resolve()
    if not path.is_relative_to(DOC_DIR.resolve()) or not path.exists():
        raise HTTPException(404, "document not found")
    return _render(path)


# ---------------------------------------------------------------------------
# Provider proxy. The project comes from the URL (/p/<project>/...), the
# X-Project header or DEFAULT_PROJECT, so existing apps can be attributed
# just by changing their base URL.
# ---------------------------------------------------------------------------
def _attribution(request: Request, project: str | None) -> Attribution:
    return Attribution(
        project=project or request.headers.get("x-project") or config.DEFAULT_PROJECT,
        agent=request.headers.get("x-agent"),
        run_id=request.headers.get("x-run-id"),
    )


@app.api_route("/anthropic/{path:path}", methods=["GET", "POST"], tags=["proxy"])
async def anthropic_proxy(path: str, request: Request) -> Response:
    return await request.app.state.proxy.forward(request, "anthropic", path, _attribution(request, None))


@app.api_route("/openai/{path:path}", methods=["GET", "POST"], tags=["proxy"])
async def openai_proxy(path: str, request: Request) -> Response:
    return await request.app.state.proxy.forward(request, "openai", path, _attribution(request, None))


@app.api_route("/p/{project}/anthropic/{path:path}", methods=["GET", "POST"], tags=["proxy"])
async def anthropic_project_proxy(project: str, path: str, request: Request) -> Response:
    return await request.app.state.proxy.forward(request, "anthropic", path, _attribution(request, project))


@app.api_route("/p/{project}/openai/{path:path}", methods=["GET", "POST"], tags=["proxy"])
async def openai_project_proxy(project: str, path: str, request: Request) -> Response:
    return await request.app.state.proxy.forward(request, "openai", path, _attribution(request, project))
