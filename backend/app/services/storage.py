from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageOps

from .. import config
from ..models import Article, UploadSession, ARTICLES, SESSIONS

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | {".pdf"}


def safe_label(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^\w.() -]+", "_", base, flags=re.UNICODE).strip(" .")
    return cleaned[:100] or "article"


def _thumbnail(image: Image.Image, output: Path) -> None:
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((500, 620), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (500, 620), "#ded9cb")
    x = (500 - image.width) // 2
    y = (620 - image.height) // 2
    canvas.paste(image, (x, y))
    canvas.save(output, "JPEG", quality=84, optimize=True)


def _new_article(upload_id: str, filename: str, image_bytes: bytes, page: int, source: dict | None = None) -> Article:
    article_id = uuid.uuid4().hex
    try:
        opened = Image.open(io.BytesIO(image_bytes))
        if opened.width * opened.height > 60_000_000:
            raise ValueError(f"{filename}: image exceeds the safe 60-megapixel processing limit.")
        image = ImageOps.exif_transpose(opened)
        image.thumbnail((6000, 6000), Image.Resampling.LANCZOS)
        image = image.convert("RGB")
        image.load()
    except ValueError as exc:
        if "safe 60-megapixel" in str(exc):
            raise
        raise ValueError(f"{filename}: the image is corrupt or unsupported.") from exc
    except Exception as exc:
        raise ValueError(f"{filename}: the image is corrupt or unsupported.") from exc
    if image.width < 300 or image.height < 300:
        raise ValueError(f"{filename}: image resolution is too small (minimum 300 × 300 pixels).")
    image_path = config.UPLOAD_DIR / f"{article_id}.jpg"
    thumbnail_path = config.THUMBNAIL_DIR / f"{article_id}.jpg"
    image.save(image_path, "JPEG", quality=95, optimize=True)
    _thumbnail(image, thumbnail_path)
    article = Article(
        id=article_id,
        upload_id=upload_id,
        filename=filename,
        path=str(image_path),
        thumbnail_path=str(thumbnail_path),
        page=page,
        source=source or {"filename": filename},
        image_width=image.width,
        image_height=image.height,
    )
    ARTICLES[article.id] = article
    return article


def add_upload(upload_id: str, filename: str, payload: bytes, source: dict | None = None) -> list[Article]:
    if not payload:
        raise ValueError(f"{filename}: file is empty.")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"{filename}: only JPG, PNG, WEBP, and PDF files are supported.")
    label = safe_label(filename)
    if ext != ".pdf":
        article = _new_article(upload_id, label, payload, 1, source)
        return [article]

    try:
        document = pymupdf.open(stream=payload, filetype="pdf")
        if document.is_encrypted:
            raise ValueError(f"{filename}: password-protected PDFs are not supported.")
        if document.page_count > config.MAX_PDF_PAGES:
            raise ValueError(f"{filename}: PDFs may contain at most {config.MAX_PDF_PAGES} pages.")
        if document.page_count == 0:
            raise ValueError(f"{filename}: the PDF contains no pages.")
        results = []
        for page_index, page in enumerate(document, start=1):
            scale = min(2.0, 6000 / max(page.rect.width, page.rect.height))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            page_source = {**(source or {}), "filename": label, "page": page_index}
            results.append(_new_article(upload_id, f"{label} · page {page_index}", pix.tobytes("png"), page_index, page_source))
        document.close()
        return results
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"{filename}: the PDF is corrupt or could not be read.") from exc


def create_session() -> UploadSession:
    session = UploadSession(id=uuid.uuid4().hex)
    SESSIONS[session.id] = session
    return session


def session_articles(upload_id: str, article_ids: list[str] | None = None) -> list[Article]:
    session = SESSIONS.get(upload_id)
    if session is None:
        raise ValueError("Upload session not found. Please upload the article images again.")
    ids = article_ids if article_ids is not None else session.article_ids
    selected = []
    for article_id in ids:
        article = ARTICLES.get(article_id)
        if article and article.upload_id == upload_id:
            selected.append(article)
    return selected
