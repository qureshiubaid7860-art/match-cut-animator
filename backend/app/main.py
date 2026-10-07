from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import config
from .api.routes import router

app = FastAPI(title="MATCH CUT · Documentary Word Highlight Generator", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.include_router(router)
app.mount("/media", StaticFiles(directory=config.DATA_DIR), name="media")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"
if FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="frontend-assets")


@app.exception_handler(Exception)
async def safe_unexpected_error(_request, _exception):
    return JSONResponse(status_code=500, content={"detail": "Something went wrong while processing the request. Check the server console and try again."})


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

    canonical = html.escape(str(request.base_url).rstrip("/") + path, quote=True)
    tags = f'<link rel="canonical" href="{canonical}" /><meta property="og:url" content="{canonical}" />'
    return document.replace("<!-- MATCH_CUT_RUNTIME_SEO -->", tags, 1)


@app.get("/{slug}/", include_in_schema=False)
@app.get("/{slug}", include_in_schema=False)
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
    base_url = str(request.base_url).rstrip("/")
    body = "User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /media/\nSitemap: " + base_url + "/sitemap.xml\n"
    return PlainTextResponse(body)


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    base_url = str(request.base_url).rstrip("/")
    paths = ("", *(f"{slug}/" for slug in SEO_PAGES))
    entries = "".join(f"<url><loc>{base_url}/{path}</loc></url>" for path in paths)
    xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + entries + "</urlset>"
    return Response(xml, media_type="application/xml")


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
