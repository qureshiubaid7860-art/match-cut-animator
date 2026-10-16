from __future__ import annotations

import io
import re
import gc
import tempfile
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
    oriented = ImageOps.exif_transpose(image)
    rgb = oriented.convert("RGB")
    if oriented is not image:
        oriented.close()
    rgb.thumbnail((500, 620), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (500, 620), "#ded9cb")
    try:
        x = (500 - rgb.width) // 2
        y = (620 - rgb.height) // 2
        canvas.paste(rgb, (x, y))
        canvas.save(output, "JPEG", quality=84, optimize=True)
    finally:
        rgb.close()
        canvas.close()


def _new_article(upload_id: str, filename: str, image_input: bytes | str | Path, page: int, source: dict | None = None) -> Article:
    article_id = uuid.uuid4().hex
    image_path = config.UPLOAD_DIR / f"{article_id}.jpg"
    thumbnail_path = config.THUMBNAIL_DIR / f"{article_id}.jpg"
    image = None
    working = None
    input_stream = image_input if isinstance(image_input, (str, Path)) else io.BytesIO(image_input)
    try:
        with Image.open(input_stream) as opened:
            if opened.width * opened.height > config.MAX_IMAGE_PIXELS:
                raise ValueError(f"{filename}: image exceeds the safe {config.MAX_IMAGE_PIXELS // 1_000_000}-megapixel processing limit.")
            if opened.format in {"JPEG", "MPO"}:
                opened.draft("RGB", (config.MAX_STORED_IMAGE_EDGE, config.MAX_STORED_IMAGE_EDGE))
            oriented = ImageOps.exif_transpose(opened)
            oriented.thumbnail((config.MAX_STORED_IMAGE_EDGE, config.MAX_STORED_IMAGE_EDGE), Image.Resampling.LANCZOS)
            working = oriented.copy()
            if oriented is not opened:
                oriented.close()
        image = working.convert("RGB")
        working.close()
        working = None
        image.load()
    except ValueError as exc:
        if image is not None:
            image.close()
        if "safe " in str(exc) and "processing limit" in str(exc):
            raise
        raise ValueError(f"{filename}: the image is corrupt or unsupported.") from exc
    except Exception as exc:
        if image is not None:
            image.close()
        raise ValueError(f"{filename}: the image is corrupt or unsupported.") from exc
    finally:
        if working is not None:
            working.close()
        if isinstance(image_input, bytes):
            input_stream.close()
    if image.width < 300 or image.height < 300:
        image.close()
        raise ValueError(f"{filename}: image resolution is too small (minimum 300 × 300 pixels).")
    image_width, image_height = image.size
    try:
        image.save(image_path, "JPEG", quality=92, optimize=True)
        _thumbnail(image, thumbnail_path)
    except Exception:
        image_path.unlink(missing_ok=True)
        thumbnail_path.unlink(missing_ok=True)
        raise
    finally:
        image.close()
    article = Article(
        id=article_id,
        upload_id=upload_id,
        filename=filename,
        path=str(image_path),
        thumbnail_path=str(thumbnail_path),
        page=page,
        source=source or {"filename": filename},
        image_width=image_width,
        image_height=image_height,
    )
    ARTICLES[article.id] = article
    return article


def add_upload(upload_id: str, filename: str, payload: bytes | str | Path, source: dict | None = None) -> list[Article]:
    if isinstance(payload, bytes):
        empty = not payload
    else:
        empty = not Path(payload).is_file() or Path(payload).stat().st_size == 0
    if empty:
        raise ValueError(f"{filename}: file is empty.")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"{filename}: only JPG, PNG, WEBP, and PDF files are supported.")
    label = safe_label(filename)
    if ext != ".pdf":
        article = _new_article(upload_id, label, payload, 1, source)
        return [article]

    document = None
    try:
        document = pymupdf.open(stream=payload, filetype="pdf") if isinstance(payload, bytes) else pymupdf.open(str(payload))
        if document.is_encrypted:
            raise ValueError(f"{filename}: password-protected PDFs are not supported.")
        if document.page_count > config.MAX_PDF_PAGES:
            raise ValueError(f"{filename}: PDFs may contain at most {config.MAX_PDF_PAGES} pages.")
        if document.page_count == 0:
            raise ValueError(f"{filename}: the PDF contains no pages.")
        results = []
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            scale = min(2.0, config.MAX_STORED_IMAGE_EDGE / max(page.rect.width, page.rect.height))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            page_source = {**(source or {}), "filename": label, "page": page_index + 1}
            with tempfile.NamedTemporaryFile(prefix="matchcut-pdf-", suffix=".png", dir=config.WORK_DIR, delete=False) as temporary:
                page_path = Path(temporary.name)
            try:
                pix.save(str(page_path))
                results.append(_new_article(upload_id, f"{label} · page {page_index + 1}", page_path, page_index + 1, page_source))
            finally:
                page_path.unlink(missing_ok=True)
                del pix
                del page
                gc.collect()
        return results
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"{filename}: the PDF is corrupt or could not be read.") from exc
    finally:
        if document is not None:
            document.close()


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
