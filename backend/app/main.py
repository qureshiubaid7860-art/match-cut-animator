from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import config
from .api.routes import router
from .jobs import cleanup_expired_videos, recover_jobs, start_render_worker, start_video_cleanup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("matchcut")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup_expired_videos()
    recover_jobs()
    start_render_worker()
    start_video_cleanup()
    logger.info(
        "MATCH CUT ready output=%sx%s fps=%s timeout=%ss cors=%s",
        config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT, config.DEFAULT_FPS, config.RENDER_TIMEOUT, config.CORS_ORIGINS,
    )
    yield


app = FastAPI(title="MATCH CUT · Documentary Word Highlight Generator", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.mount("/media/thumbnails", StaticFiles(directory=config.THUMBNAIL_DIR), name="thumbnails")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"
if FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="frontend-assets")


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exception: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": exception.errors()})


@app.exception_handler(Exception)
async def safe_unexpected_error(_request: Request, exception: Exception):
    if isinstance(exception, HTTPException):
        raise exception
    logger.exception("Unhandled server error")
    return JSONResponse(status_code=500, content={"detail": "Something went wrong while processing the request. Try again in a moment."})


@app.get("/", include_in_schema=False)
def root(request: Request):
    frontend = FRONTEND_DIST / "index.html"
    if frontend.is_file():
        return HTMLResponse(_with_runtime_seo(frontend.read_text(encoding="utf-8"), request, "/"))
    return JSONResponse({"name": "MATCH CUT", "message": "Start the frontend with `npm run dev -- --host 127.0.0.1`.", "api": "/api/health"})


SEO_PAGES = (
    "newspaper-highlight-generator",
    "text-match-cut-generator",
    "newspaper-animation",
    "documentary-text-animation",
    "newspaper-word-highlight",
)


def _with_runtime_seo(document: str, request: Request, path: str) -> str:
    import html

    canonical = html.escape(_public_base_url(request) + path, quote=True)
    tags = f'<link rel="canonical" href="{canonical}" /><meta property="og:url" content="{canonical}" />'
    return document.replace("<!-- MATCH_CUT_RUNTIME_SEO -->", tags, 1)


def _public_base_url(request: Request) -> str:
    return config.FRONTEND_URL or str(request.base_url).rstrip("/")


def editorial_page(slug: str, request: Request):
    if slug not in SEO_PAGES:
        return JSONResponse(status_code=404, content={"detail": "Page not found"})
    page = FRONTEND_DIST / slug / "index.html"
    if not page.is_file():
        page = Path(__file__).resolve().parents[2] / "frontend" / "public" / slug / "index.html"
    if not page.is_file():
        return JSONResponse(status_code=404, content={"detail": "Page not found"})
    return HTMLResponse(_with_runtime_seo(page.read_text(encoding="utf-8"), request, f"/{slug}/"))


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    base_url = _public_base_url(request)
    body = "User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /media/\nSitemap: " + base_url + "/sitemap.xml\n"
    return PlainTextResponse(body)


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    import html

    paths = ("", *(f"{slug}/" for slug in SEO_PAGES))
    base_url = _public_base_url(request)
    entries = "".join(f"<url><loc>{html.escape(base_url + '/' + path)}</loc></url>" for path in paths)
    xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + entries + "</urlset>"
    return Response(xml, media_type="application/xml")


def _editorial_endpoint(slug: str):
    def endpoint(request: Request):
        return editorial_page(slug, request)

    return endpoint


for _slug in SEO_PAGES:
    app.add_api_route(f"/{_slug}", _editorial_endpoint(_slug), methods=["GET"], include_in_schema=False)
    app.add_api_route(f"/{_slug}/", _editorial_endpoint(_slug), methods=["GET"], include_in_schema=False)


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
