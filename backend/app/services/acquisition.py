from __future__ import annotations

import hashlib
import io
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urldefrag, urlparse

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .. import config
from ..models import ARTICLES, Article, UploadSession
from ..ocr.engine import normalized_terms
from .storage import add_upload, create_session

USER_AGENT = "MATCH CUT documentary source reader/1.0 (public article transcription)"
MAX_ARTICLE_BYTES = 1_500_000
_PAGE_FONT_FAMILIES = (
    (
        "Georgia",
        ("C:/Windows/Fonts/georgia.ttf", "C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/georgiai.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
        ("/System/Library/Fonts/Supplemental/Georgia.ttf", "/System/Library/Fonts/Supplemental/Georgia Bold.ttf", "/System/Library/Fonts/Supplemental/Georgia Italic.ttf"),
    ),
    (
        "Times New Roman",
        ("C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/timesbd.ttf", "C:/Windows/Fonts/timesi.ttf"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSerif-Italic.ttf"),
        ("/System/Library/Fonts/Supplemental/Times New Roman.ttf", "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf", "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf"),
    ),
    (
        "Arial",
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
        ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf", "/Library/Fonts/Arial Italic.ttf"),
    ),
    (
        "Cambria",
        ("C:/Windows/Fonts/cambria.ttc", "C:/Windows/Fonts/cambriab.ttf", "C:/Windows/Fonts/cambriai.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerifCondensed-Italic.ttf"),
        ("/Library/Fonts/Cambria.ttf", "/Library/Fonts/Cambria Bold.ttf", "/Library/Fonts/Cambria Italic.ttf"),
    ),
    (
        "Constantia",
        ("C:/Windows/Fonts/constan.ttf", "C:/Windows/Fonts/constanb.ttf", "C:/Windows/Fonts/constani.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"),
        ("/Library/Fonts/Constantia.ttf", "/Library/Fonts/Constantia Bold.ttf", "/Library/Fonts/Constantia Italic.ttf"),
    ),
    (
        "Courier New",
        ("C:/Windows/Fonts/cour.ttf", "C:/Windows/Fonts/courbd.ttf", "C:/Windows/Fonts/couri.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf"),
        ("/Library/Fonts/Courier New.ttf", "/Library/Fonts/Courier New Bold.ttf", "/Library/Fonts/Courier New Italic.ttf"),
    ),
)
_GDELT_LOCK = threading.Lock()
_LAST_GDELT_REQUEST = 0.0


class _ArticleHTML(HTMLParser):
    """Extract article headings, short metadata, and paragraph text without a DOM dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.headings: list[str] = []
        self.paragraphs: list[str] = []
        self.meta: dict[str, str] = {}
        self._tag = ""
        self._parts: list[str] = []
        self._ignore_depth = 0
        self._ignored = {"script", "style", "svg", "noscript", "nav", "footer", "header", "aside", "form", "button"}

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if tag in self._ignored:
            self._ignore_depth += 1
            return
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            value = attrs.get("content", "").strip()
            if key and value and key in {"description", "og:description", "og:title", "author", "article:published_time"}:
                self.meta.setdefault(key, value)
        elif tag == "title":
            self._tag, self._parts = "title", []
        elif tag in {"h1", "h2", "p", "blockquote"}:
            if self._tag and self._tag != "title":
                self._flush()
            self._tag, self._parts = tag, []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignored and self._ignore_depth:
            self._ignore_depth -= 1
            return
        if tag == self._tag:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._ignore_depth or not self._tag:
            return
        value = " ".join(data.split())
        if value:
            self._parts.append(value)

    def _flush(self) -> None:
        value = " ".join(" ".join(self._parts).split())
        if value:
            if self._tag == "title":
                self.title_parts.append(value)
            elif self._tag in {"h1", "h2"}:
                self.headings.append(value)
            elif self._tag in {"p", "blockquote"} and len(value) > 35:
                self.paragraphs.append(value)
        self._tag, self._parts = "", []


def _has_word(text: str, target: str) -> bool:
    wanted = normalized_terms(target)
    if not wanted:
        return False
    tokens = normalized_terms(text)
    return any(tokens[index:index + len(wanted)] == wanted for index in range(len(tokens) - len(wanted) + 1))


def _matching_sentence(paragraphs: list[str], target: str) -> str:
    for paragraph in paragraphs:
        if not _has_word(paragraph, target):
            continue
        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        return next((sentence.strip() for sentence in sentences if _has_word(sentence, target)), paragraph[:500].strip())
    return ""


def _gdelt_request(client: httpx.Client, query: str, wanted: int):
    global _LAST_GDELT_REQUEST
    with _GDELT_LOCK:
        wait = 5.2 - (time.monotonic() - _LAST_GDELT_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_GDELT_REQUEST = time.monotonic()
        return client.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={"query": query, "mode": "ArtList", "format": "json", "maxrecords": min(50, max(20, wanted * 5)), "sort": "HybridRel"},
            timeout=12.0,
        )


def _candidate_records(word: str, wanted: int, client: httpx.Client) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    queries = [f'"{word}"', f'"{word}" (news OR report OR newspaper)', f'"{word}" article']
    for query in queries:
        payload = None
        for attempt in range(2):
            try:
                response = _gdelt_request(client, query, wanted)
                if response.status_code == 429 and attempt == 0:
                    continue
                response.raise_for_status()
                payload = response.json()
                break
            except (httpx.HTTPError, ValueError, TypeError):
                break
        if not isinstance(payload, dict):
            continue
        for item in payload.get("articles", []):
            url = str(item.get("url") or "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or url in seen:
                continue
            seen.add(url)
            records.append({
                "url": url,
                "title": str(item.get("title") or "").strip(),
                "publication": str(item.get("domain") or parsed.netloc).strip(),
                "date": str(item.get("seendate") or "").strip(),
                "provider": "GDELT DOC",
            })
        if len(records) >= wanted * 4:
            break

    if config.NEWSAPI_KEY and len(records) < wanted * 3:
        try:
            response = client.get(
                "https://newsapi.org/v2/everything",
                params={"q": f'"{word}"', "sortBy": "relevancy", "pageSize": min(100, wanted * 6), "language": "en", "apiKey": config.NEWSAPI_KEY},
                timeout=12.0,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") == "ok":
                for item in payload.get("articles", []):
                    url = str(item.get("url") or "").strip()
                    parsed = urlparse(url)
                    if parsed.scheme not in {"http", "https"} or not parsed.netloc or url in seen:
                        continue
                    seen.add(url)
                    records.append({
                        "url": url,
                        "title": str(item.get("title") or "").strip(),
                        "publication": str((item.get("source") or {}).get("name") or parsed.netloc).strip(),
                        "date": str(item.get("publishedAt") or "").strip(),
                        "provider": "NewsAPI",
                    })
        except (httpx.HTTPError, ValueError, TypeError):
            pass
    return records[: max(12, wanted * 4)]


def _read_article(record: dict, target: str, timeout: float = 8.0) -> dict | None:
    url = record["url"]
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "html" not in content_type and "xhtml" not in content_type:
                    return None
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes(16384):
                    size += len(chunk)
                    if size > MAX_ARTICLE_BYTES:
                        break
                    chunks.append(chunk)
                body = b"".join(chunks)
                encoding = response.encoding or "utf-8"
                final_url = str(response.url)
        parser = _ArticleHTML()
        parser.feed(body.decode(encoding, errors="replace"))
        title = parser.headings[0] if parser.headings else parser.meta.get("og:title") or (parser.title_parts[0] if parser.title_parts else record.get("title", ""))
        description = parser.meta.get("og:description") or parser.meta.get("description", "")
        paragraphs = []
        if description and description not in parser.paragraphs:
            paragraphs.append(description)
        paragraphs.extend(parser.paragraphs)
        # Keep only real publisher text and a short excerpt around the exact word.
        lead = _matching_sentence([title, *paragraphs], target)
        if not lead:
            return None
        unique: list[str] = []
        for paragraph in [*paragraphs, title]:
            cleaned = " ".join(paragraph.split())
            if cleaned and cleaned not in unique:
                unique.append(cleaned[:900])
        host = urlparse(final_url).netloc.lower()
        return {
            "url": final_url,
            "title": title[:220] or record.get("title", "Public article"),
            "publication": record.get("publication") or host,
            "date": record.get("date", ""),
            "lead": lead[:650],
            "paragraphs": unique,
            "source_provider": record.get("provider", "Public web article"),
        }
    except (httpx.HTTPError, UnicodeError, ValueError, OSError):
        return None


def _page_font_style(seed: int) -> tuple[int, str]:
    style_index = seed % len(_PAGE_FONT_FAMILIES)
    return style_index, _PAGE_FONT_FAMILIES[style_index][0]


def _font(size: int, bold: bool = False, italic: bool = False, family_index: int = 0):
    font_variant = 1 if bold else 2 if italic else 0
    selected_family = family_index % len(_PAGE_FONT_FAMILIES)
    candidates = [
        paths[font_variant]
        for paths in _PAGE_FONT_FAMILIES[selected_family][1:]
    ]
    for alternate_index, alternate_family in enumerate(_PAGE_FONT_FAMILIES):
        if alternate_index != selected_family:
            candidates.extend(paths[font_variant] for paths in alternate_family[1:])
    candidates.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
        if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"
        if italic else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/Library/Fonts/Georgia.ttf",
    ])
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, value: str, font, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in value.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _draw_block(draw, value: str, xy: tuple[int, int], width: int, font, fill, spacing: int = 8, max_lines: int = 99) -> int:
    x, y = xy
    lines = _wrap(draw, value, font, width)[:max_lines]
    line_height = max(font.size + spacing, 20)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _page_image(
    title: str,
    lead: str,
    paragraphs: list[str],
    label: str,
    source_line: str,
    seed: int,
    target_word: str | None = None,
) -> tuple[bytes, list[float] | None]:
    family_index, _ = _page_font_style(seed)
    width, height = 1500, 2100
    paper = Image.new("RGB", (width, height), "#eee9dc")

    grain = Image.effect_noise((width, height), 3).convert("L")
    grain_rgb = Image.merge("RGB", (grain, grain, grain))
    paper = Image.blend(paper, grain_rgb, 0.025)

    draw = ImageDraw.Draw(paper)
    ink, muted, accent = "#25241f", "#777366", "#8d3829"
    margin = 105
    is_fiction = label.startswith("FICTIONAL")
    target_bbox = None

    draw.text(
        (margin, 76),
        "MATCH CUT  /  EDITORIAL TRANSCRIPTION",
        font=_font(23, bold=True, family_index=family_index),
        fill=ink,
    )

    draw.text(
        (width - margin, 80),
        "FICTIONAL" if is_fiction else "PUBLIC SOURCE",
        font=_font(21, bold=True, family_index=family_index),
        fill=accent if is_fiction else muted,
        anchor="ra",
    )

    draw.line((margin, 122, width - margin, 122), fill=ink, width=3)

    draw.text(
        (margin, 151),
        label,
        font=_font(18, bold=True, family_index=family_index),
        fill=accent if is_fiction else muted,
    )

    headline_font = _font(
        91 if len(title) < 70 else 76,
        bold=True,
        family_index=family_index,
    )

    # For fictional pages, deliberately draw the complete headline as one
    # line where possible so the target word has a deterministic bbox.
    headline_y = 218

    if is_fiction and target_word and target_word in title:
        before, after = title.split(target_word, 1)

        before_bbox = draw.textbbox(
            (0, 0),
            before,
            font=headline_font,
        )
        target_width = draw.textbbox(
            (0, 0),
            target_word,
            font=headline_font,
        )[2]

        target_x = margin + before_bbox[2]

        # Keep the existing headline appearance.
        y = _draw_block(
            draw,
            title,
            (margin, headline_y),
            width - margin * 2,
            headline_font,
            ink,
            spacing=9,
            max_lines=4,
        )

        # Exact target bbox in the generated page.
        target_bbox = [
            float(target_x),
            float(headline_y),
            float(target_width),
            float(headline_font.size + 12),
        ]
    else:
        y = _draw_block(
            draw,
            title,
            (margin, headline_y),
            width - margin * 2,
            headline_font,
            ink,
            spacing=9,
            max_lines=4,
        )

    draw.text(
        (margin, y + 10),
        source_line[:140],
        font=_font(20, family_index=family_index),
        fill=muted,
    )

    y += 67
    draw.line((margin, y, width - margin, y), fill="#9a9587", width=1)
    y += 33

    lead_font = _font(39, italic=True, family_index=family_index)

    y = _draw_block(
        draw,
        lead,
        (margin, y),
        width - margin * 2,
        lead_font,
        ink,
        spacing=11,
        max_lines=5,
    )

    y += 30
    draw.line((margin, y, width - margin, y), fill="#b8b2a2", width=1)
    y += 38

    content = " ".join(paragraphs)
    column_count = 2 if seed % 3 == 1 else 3
    col_gap = 42
    col_width = (
        width - margin * 2 - col_gap * (column_count - 1)
    ) // column_count

    col_x = [
        margin + index * (col_width + col_gap)
        for index in range(column_count)
    ]
    col_y = [y for _ in range(column_count)]

    body_font = _font(27, family_index=family_index)
    remaining = content

    for _ in range(3):
        while remaining and min(col_y) < height - 180:
            index = min(
                range(column_count),
                key=lambda i: col_y[i],
            )

            if col_y[index] > height - 220:
                col_y[index] = height
                continue

            text = remaining[:]
            lines = _wrap(
                draw,
                text,
                body_font,
                col_width,
            )

            available = max(
                1,
                int(
                    (height - 190 - col_y[index])
                    / (body_font.size + 11)
                ),
            )

            chosen = lines[:available]
            consumed = " ".join(chosen)

            if not consumed:
                break

            for line in chosen:
                draw.text(
                    (col_x[index], col_y[index]),
                    line,
                    font=body_font,
                    fill=ink,
                )
                col_y[index] += body_font.size + 11

            consumed_words = len(consumed.split())
            remaining = " ".join(
                remaining.split()[consumed_words:]
            )

            if col_y[index] < height - 180:
                col_y[index] += 18

            if not remaining:
                break

        if not remaining:
            break

    if is_fiction:
        draw.text(
            (width // 2, height - 115),
            source_line[:165],
            font=_font(
                19,
                bold=True,
                family_index=family_index,
            ),
            fill=accent,
            anchor="mm",
        )
    else:
        draw.text(
            (margin, height - 115),
            source_line[:165],
            font=_font(
                15,
                family_index=family_index,
            ),
            fill=muted,
        )

        draw.text(
            (width - margin, height - 115),
            "PUBLIC ARTICLE EXCERPT",
            font=_font(
                15,
                bold=True,
                family_index=family_index,
            ),
            fill=muted,
            anchor="ra",
        )

    paper = paper.filter(
        ImageFilter.GaussianBlur(radius=0.12)
    )

    image_bytes = io.BytesIO()
    paper.save(
        image_bytes,
        "JPEG",
        quality=93,
        optimize=True,
    )

    return image_bytes.getvalue(), target_bbox
# yah tak 12345678


def _real_page(story: dict, target: str, index: int) -> tuple[str, bytes, dict]:
    title = story["title"]
    lead = story["lead"]
    content = [paragraph for paragraph in story["paragraphs"] if paragraph != lead and paragraph != title]
    # Limit copied source text to a brief, attributed excerpt.
    content = content[:6]
    total = 0
    brief: list[str] = []
    for paragraph in content:
        take = paragraph[: max(0, 1500 - total)]
        if take:
            brief.append(take)
            total += len(take)
        if total >= 1500:
            break
    host = urlparse(story["url"]).netloc
    source_line = f"{story.get('publication') or host}  ·  {host}  ·  public article excerpt"
    _, font_style = _page_font_style(index)
    target_bbox = None
    source = {
        "publication": story.get("publication") or host,
        "headline": title,
        "date": story.get("date", ""),
        "source_url": story["url"],
        "provider": story.get("source_provider", "Public web article"),
        "source_kind": "article_reconstruction",
        "generated": False,
        "font_style": font_style,
        "representation": "Locally typeset reconstruction using an attributed excerpt from the linked public article.",
        "target_word": target,
    }
    page, _ = _page_image(
    title,
    lead,
    brief,
    "PUBLIC ARTICLE · RECONSTRUCTED EXCERPT",
    source_line,
    index,
)
    return f"public-article-{index + 1}.jpg", page, source


_FICTIONAL_HEADLINES = [
    "The word {word} returns to an imagined daily briefing",
    "A closer look at the term {word}",
    "Notes from a fictional newsroom: {word}",
    "How {word} might read in tomorrow's paper",
    "The many meanings of {word}, in an imagined report",
    "An editorial exercise built around {word}",
    "A word in focus: {word}",
    "Inside a fictional report on {word}",
]

_FICTIONAL_LEADS = [
    "This invented briefing places {word} inside a clearly fictional editorial exercise.",
    "A made-up daily page uses {word} as its subject, without describing a real event.",
    "In this imagined edition, {word} anchors a short piece of fictional editorial copy.",
    "This fictional page follows the word {word} through a sample newsroom layout.",
    "The term {word} appears here in an invented article, created for this visual sequence.",
    "An imaginary newspaper uses {word} to explore the shape of a changing headline.",
    "This made-up report is built around {word}; its setting and details are invented.",
    "The word {word} leads this fictional editorial study of printed page design.",
]

_FICTIONAL_BODY = [
    (
        "This page is a work of fiction and makes no claim about a real person, place, organization or event.",
        "Its varied columns and headlines belong to an invented newsroom, with no real publisher represented.",
        "The word {word} is included as the subject of an editorial exercise, not as a report of current events.",
    ),
    (
        "No real event or institution is described in this imagined article.",
        "The typography follows a familiar print format while the setting and all supporting copy remain fictional.",
        "Here, {word} provides a shared thread through a sample headline and a made-up editorial page.",
    ),
    (
        "The newsroom and article on this page are invented for a documentary-style visual sequence.",
        "There are no real reporters, quotations or organizations behind the text.",
        "A changing arrangement of columns keeps {word} in view while the fictional page design shifts.",
    ),
    (
        "This imagined edition contains no verified news, named sources or factual claims.",
        "Its newspaper-like structure is an editorial exercise, created without a publisher's name or mark.",
        "The word {word} appears in the fictional copy as a visual subject only.",
    ),
    (
        "The page presents an invented editorial scenario rather than a real article.",
        "Its headline, column layout and supporting sentences were created locally for this visual study.",
        "The term {word} links the sample copy across this fictional page.",
    ),
    (
        "This sample is clearly fictional and does not refer to a real publication or event.",
        "An imagined editor has arranged the copy in a traditional printed format.",
        "Within that made-up setting, {word} is the subject that carries from one line to another.",
    ),
    (
        "Every detail on this page belongs to an invented newsroom exercise.",
        "No real person, organization, location or quotation is represented in the copy.",
        "The word {word} gives this fictional editorial its recurring subject.",
    ),
    (
        "This is a fabricated editorial page with no connection to a real news report.",
        "The print-inspired layout changes from page to page while the fictional setting stays unnamed.",
        "The word {word} appears as the subject of this imagined piece.",
    ),
]


def _fictional_page(word: str, index: int) -> tuple[str, bytes, dict]:
    headline = _FICTIONAL_HEADLINES[index % len(_FICTIONAL_HEADLINES)].format(word=word)
    lead = _FICTIONAL_LEADS[index % len(_FICTIONAL_LEADS)].format(word=word)
    paragraphs = [line.format(word=word) for line in _FICTIONAL_BODY[index % len(_FICTIONAL_BODY)]]
    _, font_style = _page_font_style(index)
    source = {
        "publication": "Fictional editorial",
        "headline": headline,
        "source_url": "",
        "provider": "Local fallback",
        "source_kind": "fictional_fallback",
        "generated": True,
        "edition": index + 1,
        "font_style": font_style,
        "representation": "Clearly marked fictional editorial page generated locally because public article sources were insufficient.",
        "target_word": word,
    }
    page, target_bbox = _page_image(
        headline,
        lead,
        paragraphs,
        "FICTIONAL EDITORIAL · GENERATED CONTENT",
        f"FICTIONAL EDITORIAL · EDITION {index + 1:02d} · NOT A NEWS REPORT",
        500 + index,
        target_word=word,
    )

    source["bbox"] = target_bbox
    source["confidence"] = 1.0
    source["found"] = target_bbox is not None

    return f"fictional-editorial-{index + 1}.jpg", page, source

def _quality(article: Article) -> float:
    if not article.bbox or not article.image_width or not article.image_height:
        return 0.0
    relative_height = article.bbox[3] / max(article.image_height, 1)
    size = min(relative_height / 0.045, 1.0)
    resolution = min(min(article.image_width, article.image_height) / 1200, 1.0)
    center_x = (article.bbox[0] + article.bbox[2] / 2) / article.image_width
    center_y = (article.bbox[1] + article.bbox[3] / 2) / article.image_height
    center = 1.0 - min(abs(center_x - 0.5) + abs(center_y - 0.5) * 0.5, 1.0)
    return 0.49 * article.confidence + 0.24 * size + 0.15 * resolution + 0.12 * center


def order_for_match_cuts(articles: list[Article]) -> list[Article]:
    """Rank for readability, then use pairwise geometry to build a low-change path."""
    unique: list[Article] = []
    seen_ids: set[str] = set()
    seen_sources: set[str] = set()
    seen_pages: set[str] = set()
    for article in articles:
        if article.id in seen_ids:
            continue
        source_url = str((article.source or {}).get("source_url") or "").strip()
        source_url = urldefrag(source_url).url if source_url else ""
        if source_url and source_url in seen_sources:
            continue
        try:
            page_digest = hashlib.sha256(Path(article.path).read_bytes()).hexdigest()
        except OSError:
            page_digest = ""
        if page_digest and page_digest in seen_pages:
            continue
        seen_ids.add(article.id)
        if source_url:
            seen_sources.add(source_url)
        if page_digest:
            seen_pages.add(page_digest)
        unique.append(article)
    articles = unique
    if len(articles) < 2:
        return list(articles)
    quality = {article.id: _quality(article) for article in articles}

    def geometry(article: Article) -> tuple[float, float]:
        box = article.bbox or [0, 0, 1, 1]
        page_h = max(article.image_height, 1)
        return (box[2] / max(box[3], 1), box[3] / page_h)

    # Build all candidate-to-candidate transition costs before selecting the path.
    costs: dict[tuple[str, str], float] = {}
    for left in articles:
        lw, lh = geometry(left)
        for right in articles:
            if left.id == right.id:
                continue
            rw, rh = geometry(right)
            geometry_cost = abs(math.log(max(lw, .01) / max(rw, .01))) + 0.35 * abs(math.log(max(lh, .001) / max(rh, .001)))
            costs[left.id, right.id] = geometry_cost + 0.15 * (1 - quality[right.id])
    remaining = set(article.id for article in articles)
    by_id = {article.id: article for article in articles}
    current = max(articles, key=lambda item: quality[item.id])
    ordered = [current]
    remaining.remove(current.id)
    while remaining:
        current_style = (current.source or {}).get("font_style")
        different_style_ids = {
            candidate for candidate in remaining
            if (by_id[candidate].source or {}).get("font_style") != current_style
        }
        candidates = different_style_ids or remaining
        next_id = min(candidates, key=lambda candidate: costs[current.id, candidate])
        current = by_id[next_id]
        ordered.append(current)
        remaining.remove(next_id)
    return ordered


def acquire_sources(
    target_word: str,
    requested_count: int,
    progress: Callable[[str, int, str], None] | None = None,
) -> tuple[UploadSession, list[Article]]:
    if requested_count > config.MAX_ARTICLES:
        raise RuntimeError(f"The configured source limit is {config.MAX_ARTICLES} pages; choose a smaller article count.")
    session = create_session()
    session.target_word = target_word
    if progress:
        progress("01 — Searching Sources", 4, f"Searching public article indexes for “{target_word}”")
    candidates: list[dict] = []
    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
            candidates = _candidate_records(target_word, requested_count, client)
    except httpx.HTTPError:
        candidates = []
    if progress:
        progress("02 — Downloading Sources", 14, f"Checking up to {len(candidates)} public article pages")

    stories: list[dict] = []
    if candidates:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_read_article, candidate, target_word) for candidate in candidates]
            for future in as_completed(futures):
                story = future.result()
                if story:
                    stories.append(story)
                if len(stories) >= requested_count * 2:
                    break

    added: list[Article] = []
    real_limit = min(requested_count * 2, config.MAX_ARTICLES)
    for index, story in enumerate(stories[:real_limit]):
        try:
            filename, payload, source = _real_page(story, target_word, index)
            article = add_upload(session.id, filename, payload, source)[0]
            session.article_ids.append(article.id)
            added.append(article)
        except (ValueError, OSError):
            continue

    if progress:
        progress("03 — Running OCR", 31, f"Reading target-word positions on {len(added)} retrieved article pages")
    from .analysis import analyze_articles

    if added:
        analyze_articles(added, target_word)
    matches = [article for article in added if article.found and article.bbox and article.confidence >= 0.30]
    matches = sorted(matches, key=_quality, reverse=True)
    kept_real = matches[:requested_count]
    kept_real_ids = {article.id for article in kept_real}
    for article in added:
        if article.id not in kept_real_ids:
            Path(article.path).unlink(missing_ok=True)
            Path(article.thumbnail_path).unlink(missing_ok=True)
            ARTICLES.pop(article.id, None)
    session.article_ids = [article.id for article in kept_real]
    matches = kept_real

    if progress:
        progress("04 — Finding Target Word", 43, f"OCR confirmed {len(matches)} public article matches")
    missing = max(0, requested_count - len(matches))
    if missing and progress:
        progress("05 — Selecting Best Matches", 48, f"Searching more results and preparing {missing} clearly fictional fallback page{'s' if missing != 1 else ''}")

    generated: list[Article] = []
    for index in range(missing):
        filename, payload, source = _fictional_page(target_word, index)
        try:
            article = add_upload(session.id, filename, payload, source)[0]
            session.article_ids.append(article.id)
            generated.append(article)
        except (ValueError, OSError):
            continue
        # Fictional pages already contain a deterministic target-word bbox.
    # Do not run OCR again on these locally generated pages.
    for article in generated:
        source = article.source or {}
        article.analysis_target = target_word
        article.found = bool(source.get("found"))
        article.confidence = float(source.get("confidence") or 1.0)
        article.bbox = source.get("bbox")

    generated_matches = [
        article
        for article in generated
        if article.found and article.bbox
    ]
    selected = order_for_match_cuts([*matches[:requested_count], *generated_matches])[:requested_count]
    if len(selected) < 3:
        raise RuntimeError(f"The automated source pipeline found only {len(selected)} OCR-readable matches for “{target_word}”.")
    session.article_ids = [article.id for article in selected]
    session.analysis_target = target_word
    if progress:
        real_count = sum(1 for article in selected if not (article.source or {}).get("generated"))
        progress("06 — Building Match Cut Timeline", 58, f"Ordered {len(selected)} sources for stable word framing ({real_count} public, {len(selected) - real_count} fictional)")
    return session, selected
