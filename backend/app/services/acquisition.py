from __future__ import annotations

import hashlib
import io
import math
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urldefrag, urlparse
from xml.etree import ElementTree

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
        self.article_paragraphs: list[str] = []
        self.meta: dict[str, str] = {}
        self._tag = ""
        self._parts: list[str] = []
        self._ignore_depth = 0
        self._article_depth = 0
        self._ignored = {"script", "style", "svg", "noscript", "nav", "footer", "header", "aside", "form", "button"}

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if tag in self._ignored:
            self._ignore_depth += 1
            return
        if tag == "article":
            self._article_depth += 1
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
        if tag == "article" and self._article_depth:
            self._article_depth -= 1

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
                if self._article_depth:
                    self.article_paragraphs.append(value)
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


def _rss_candidate_records(word: str, wanted: int, client: httpx.Client, seen: set[str]) -> list[dict]:
    records: list[dict] = []
    per_feed = min(72, max(12, wanted * 2))
    feeds = (
        (
            "Bing News RSS",
            "https://www.bing.com/news/search",
            {"q": f'"{word}"', "format": "RSS"},
        ),
    )
    for provider, endpoint, params in feeds:
        try:
            response = client.get(endpoint, params=params, timeout=12.0)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                continue
            root = ElementTree.fromstring(response.content)
        except (httpx.HTTPError, ElementTree.ParseError, ValueError):
            continue

        added = 0
        for item in root.findall(".//item"):
            url = str(item.findtext("link") or "").strip()
            parsed = urlparse(url)
            if provider == "Bing News RSS" and parsed.netloc.casefold().endswith("bing.com"):
                direct_url = parse_qs(parsed.query).get("url", [""])[0].strip()
                direct = urlparse(direct_url)
                if direct.scheme in {"http", "https"} and direct.netloc:
                    url, parsed = direct_url, direct
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or url in seen:
                continue
            title = str(item.findtext("title") or "").strip()
            if not title:
                continue
            seen.add(url)
            records.append({
                "url": url,
                "title": title,
                "publication": str(item.findtext("source") or parsed.netloc).strip(),
                "date": str(item.findtext("pubDate") or "").strip(),
                "provider": provider,
            })
            added += 1
            if added >= per_feed:
                break
    return records


def _candidate_records(word: str, wanted: int, client: httpx.Client) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    queries = [f'"{word}"', f'"{word}" (news OR report OR newspaper)', f'"{word}" article']
    for query in queries:
        try:
            response = _gdelt_request(client, query, wanted)
            if response.status_code == 429:
                break
            response.raise_for_status()
            payload = response.json()
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

    # Include direct publisher URLs even when GDELT returns many candidate links.
    # It can surface inaccessible or aggregator pages that the reader later rejects.
    rss_records = _rss_candidate_records(word, wanted, client, seen)
    bing_records = [record for record in rss_records if record.get("provider") == "Bing News RSS"]
    combined = records[: wanted * 2] + bing_records[: wanted * 2]
    return combined[: max(12, wanted * 4)]


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
        generic_titles = {"home", "menu", "ana menü", "ana menu", "news", "latest news", "article"}
        title_candidates = [
            record.get("title", ""),
            parser.meta.get("og:title", ""),
            *parser.headings,
            *parser.title_parts,
        ]
        title = next(
            (
                " ".join(candidate.split())
                for candidate in title_candidates
                if candidate
                and len(candidate.strip()) >= 18
                and candidate.strip().casefold() not in generic_titles
            ),
            next((candidate.strip() for candidate in title_candidates if candidate and candidate.strip()), "Public article"),
        )
        description = parser.meta.get("og:description") or parser.meta.get("description", "")
        body_paragraphs = parser.article_paragraphs
        if sum(map(len, body_paragraphs)) < 240:
            body_paragraphs = parser.paragraphs
        article_text: list[str] = []
        for paragraph in body_paragraphs:
            cleaned = paragraph.strip()
            if article_text and len(cleaned) < 90 and cleaned.endswith(":"):
                break
            article_text.append(cleaned)
        body_paragraphs = article_text
        paragraphs = []
        if description and description not in body_paragraphs:
            paragraphs.append(description)
        paragraphs.extend(body_paragraphs)
        boilerplate = (
            "comprehensive, up-to-date news coverage",
            "aggregated from sources all over the world by google news",
            "sign in to google news",
            "accept all cookies",
        )
        paragraphs = [
            paragraph.strip()
            for paragraph in paragraphs
            if len(paragraph.strip()) > 35
            and not any(marker in paragraph.casefold() for marker in boilerplate)
        ]
        final_host = urlparse(final_url).netloc.casefold().split(":", 1)[0]
        if final_host == "news.google.com" or final_host.endswith(".news.google.com") or final_host in {"google.com", "www.google.com", "bing.com", "www.bing.com"}:
            return None
        body_chars = sum(len(paragraph) for paragraph in paragraphs if paragraph.strip() != title.strip())
        if body_chars < 500 or not _has_word(f"{title} {' '.join(paragraphs)}", target):
            return None
        # Keep the lead distinct from the headline and use publisher text only.
        lead = _matching_sentence(paragraphs, target)
        if not lead:
            lead = next((paragraph for paragraph in paragraphs if len(paragraph) > 35), title)
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


def _contains_exact_phrase(text: str, phrase: str) -> bool:
    wanted = normalized_terms(phrase)
    words = normalized_terms(text)
    return bool(wanted) and any(words[index:index + len(wanted)] == wanted for index in range(len(words) - len(wanted) + 1))


def _page_image(
    title: str,
    lead: str,
    paragraphs: list[str],
    label: str,
    source_line: str,
    seed: int,
    target_word: str | None = None,
    masthead: str | None = None,
) -> tuple[bytes, list[float] | None]:
    family_index, _ = _page_font_style(seed)
    width, height = 1500, 2100
    paper = Image.new("RGB", (width, height), "#eee8d8")

    # Fine fibers and low-contrast mottling make the page read as printed stock.
    grain = Image.effect_noise((width, height), 42).filter(ImageFilter.GaussianBlur(0.35)).convert("L")
    grain_rgb = Image.merge("RGB", (grain, grain, grain))
    paper = Image.blend(paper, grain_rgb, 0.085)
    mottling = Image.effect_noise((width, height), 36).filter(ImageFilter.GaussianBlur(9)).convert("L")
    paper = Image.blend(paper, Image.merge("RGB", (mottling, mottling, mottling)), 0.06)

    # Faded, irregular edge wear; the center stays clear enough for OCR and reading.
    edge_wear = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    wear_draw = ImageDraw.Draw(edge_wear)
    wear_draw.rectangle((0, 0, width, height), outline=(78, 67, 48, 18), width=20)
    wear_draw.line((18, 8, width - 30, 5), fill=(82, 70, 49, 10), width=7)
    wear_draw.line((9, 20, 12, height - 25), fill=(82, 70, 49, 9), width=8)
    edge_wear = edge_wear.filter(ImageFilter.GaussianBlur(15))
    paper = Image.alpha_composite(paper.convert("RGBA"), edge_wear).convert("RGB")

    draw = ImageDraw.Draw(paper)
    ink, muted, accent = "#25241f", "#777366", "#8d3829"
    margin = 105
    is_fiction = label.startswith("FICTIONAL")
    target_bbox = None

    masthead_text = masthead or ("THE DAILY EDITION" if is_fiction else "PUBLIC ARTICLE")
    draw.text(
        (margin, 62),
        masthead_text.upper()[:42],
        font=_font(39, bold=True, family_index=family_index),
        fill=ink,
    )

    draw.text(
        (width - margin, 80),
        "FICTIONAL MOCK-UP" if is_fiction else "RECONSTRUCTED",
        font=_font(17, bold=True, family_index=family_index),
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
        # Fit the complete fictional headline on one line so the
        # target-word coordinates remain deterministic and valid.
        available_width = width - margin * 2

        font_size = 91 if len(title) < 70 else 76

        while font_size >= 42:
            candidate_font = _font(
                font_size,
                bold=True,
                family_index=family_index,
            )

            title_width = draw.textbbox(
                (0, 0),
                title,
                font=candidate_font,
            )[2]

            if title_width <= available_width:
                headline_font = candidate_font
                break

            font_size -= 3
        else:
            headline_font = _font(
                42,
                bold=True,
                family_index=family_index,
            )

        # Draw the complete headline as one line.
        draw.text(
            (margin, headline_y),
            title,
            font=headline_font,
            fill=ink,
        )

        # Calculate the target word position inside that same line.
        before, target_part, _after = title.partition(target_word)

        before_width = draw.textbbox(
            (0, 0),
            before,
            font=headline_font,
        )[2]

        target_width = draw.textbbox(
            (0, 0),
            target_part,
            font=headline_font,
        )[2]

        target_x = margin + before_width

        # Clamp bbox so it can never leave the page.
        target_x = max(
            float(margin),
            min(
                float(target_x),
                float(width - margin - target_width),
            ),
        )

        target_width = min(
            float(target_width),
            float(width - margin - target_x),
        )

        target_bbox = [
            target_x,
            float(headline_y),
            target_width,
            float(headline_font.size + 12),
        ]

        y = headline_y + headline_font.size + 12

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

    if is_fiction and target_word:
        prefixes = (
            "Editors examine ",
            "Researchers trace ",
            "Printed notes discuss ",
            "Commentators consider ",
            "Observers review ",
            "A columnist weighs ",
            "This report follows ",
            "Context surrounds ",
        )
        suffixes = (
            " — continued in the next column.",
            " — notes from an imagined desk.",
            " — a fictional editorial note.",
            " — filed in a sample edition.",
            " — with fictional context only.",
        )
        available_width = width - margin * 2
        choices = []
        for prefix in prefixes:
            if _contains_exact_phrase(prefix, target_word):
                continue
            for suffix in suffixes:
                if _contains_exact_phrase(suffix, target_word):
                    continue
                target_line = prefix + target_word + suffix
                target_font_size = 46
                while target_font_size > 20:
                    candidate_font = _font(target_font_size, family_index=family_index)
                    if draw.textbbox((0, 0), target_line, font=candidate_font)[2] <= available_width:
                        break
                    target_font_size -= 2
                target_font = _font(target_font_size, family_index=family_index)
                prefix_width = draw.textlength(prefix, font=target_font)
                word_width = draw.textlength(target_word, font=target_font)
                center_x = margin + prefix_width + word_width / 2
                choices.append((abs(center_x - width / 2), target_line, prefix, target_font, target_font_size))
        if not choices:
            target_line, prefix, target_font, target_font_size = (
                f"— {target_word} —",
                "— ",
                _font(46, family_index=family_index),
                46,
            )
        else:
            _, target_line, prefix, target_font, target_font_size = choices[(seed * 7) % len(choices)]
        y += 10
        draw.text((margin, y), target_line, font=target_font, fill=ink)
        target_x = margin + draw.textlength(prefix, font=target_font)
        target_bounds = draw.textbbox((target_x, y), target_word, font=target_font)
        target_bbox = [
            float(target_bounds[0]),
            float(target_bounds[1]),
            float(target_bounds[2] - target_bounds[0]),
            float(target_bounds[3] - target_bounds[1]),
        ]
        y += max(target_font_size + 18, target_bounds[3] - target_bounds[1] + 18)

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
    source_paragraphs = [paragraph for paragraph in story["paragraphs"] if paragraph != title]
    if lead and source_paragraphs and all(paragraph == lead for paragraph in source_paragraphs):
        # Put a single article paragraph in the body once instead of
        # repeating it as both the deck and the article text.
        lead = ""
        content = source_paragraphs
    else:
        content = []
    if lead:
        for paragraph in source_paragraphs:
            if paragraph == lead:
                continue
            body_paragraph = paragraph
            lead_words = [match.group(0).casefold() for match in re.finditer(r"\w+", lead)]
            body_matches = list(re.finditer(r"\w+", body_paragraph))
            shared_prefix = 0
            while (
                shared_prefix < min(len(lead_words), len(body_matches))
                and body_matches[shared_prefix].group(0).casefold() == lead_words[shared_prefix]
            ):
                shared_prefix += 1
            if shared_prefix >= 8:
                body_paragraph = body_paragraph[body_matches[shared_prefix - 1].end():]
            body_paragraph = body_paragraph.lstrip(" .;:—–-\t")
            if len(body_paragraph) > 35 and body_paragraph not in content:
                content.append(body_paragraph)
    # Limit copied source text to a brief, attributed excerpt.
    content = content[:10]
    total = 0
    brief: list[str] = []
    for paragraph in content:
        take = paragraph[: max(0, 2400 - total)]
        if take:
            brief.append(take)
            total += len(take)
        if total >= 2400:
            break
    host = urlparse(story["url"]).netloc
    publication = story.get("publication") or host
    date = str(story.get("date") or "").strip()
    month_names = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    date_label = ""
    if len(date) >= 8 and date[:8].isdigit():
        month = int(date[4:6])
        if 1 <= month <= 12:
            date_label = f"{date[6:8]} {month_names[month - 1]} {date[:4]}"
    source_line = f"ARTICLE EXCERPT  ·  {date_label}" if date_label else "PUBLIC ARTICLE EXCERPT"
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
        masthead=publication,
    )
    return f"public-article-{index + 1}.jpg", page, source


_FICTIONAL_HEADLINES = [
    "A note from an imagined daily briefing",
    "A closer look at a changing report",
    "Notes from a fictional newsroom",
    "How a story might read in tomorrow's paper",
    "A short account from an imagined edition",
    "An editorial exercise in print",
    "A word in focus across the page",
    "Inside a fictional report",
]

_FICTIONAL_SECTIONS = (
    "Margins and meaning",
    "The copy desk",
    "Paper and type",
    "A page in progress",
    "Column notes",
    "Reading the layout",
    "Ink and spacing",
    "The printed form",
    "Editorial rhythm",
)

_FICTIONAL_LEADS = [
    "This invented briefing presents an editorial exercise, without describing a real event.",
    "A made-up daily page studies one subject in a traditional print layout.",
    "In this imagined edition, an editor has arranged a short piece of fictional copy.",
    "This fictional page follows a sample newsroom layout from headline to report.",
    "An invented article provides the surrounding text for this visual sequence.",
    "An imaginary newspaper explores the shape of a changing headline.",
    "This made-up report uses an unnamed setting and invented editorial details.",
    "A fictional editorial studies the look and rhythm of a printed page.",
]

_FICTIONAL_LEAD_DETAILS = (
    "The headline, opening line, and columns are arranged as a visual study.",
    "Its margins and type show one way a printed page can guide the eye.",
    "The page is composed as a design exercise, with all supporting copy invented.",
    "A different mix of spacing and column widths gives this sample its own rhythm.",
    "The surrounding copy describes the layout itself, not a real-world event.",
    "Its details are invented to make each page turn visually distinct.",
    "This edition keeps its setting unnamed and its sample copy clearly fictional.",
    "The composition changes from page to page while the highlighted phrase stays clear.",
    "Every line around the highlighted phrase was written for this mock newspaper page.",
)

_FICTIONAL_LAYOUT_SUBJECTS = (
    "masthead",
    "headline",
    "opening line",
    "first column",
    "section label",
    "page number",
    "thin rule",
    "margin note",
    "closing column",
)

_FICTIONAL_LAYOUT_STYLES = (
    "open",
    "compact",
    "two-column",
    "three-column",
    "wide-margin",
    "staggered",
    "balanced",
    "close-set",
)

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

_FICTIONAL_COLUMN_COPY = (
    "The column begins with a short introduction, then lets the detail arrive one piece at a time. Small paragraphs and narrow measure make a printed page easier to scan from one line to the next.",
    "A headline gives the reader a point of entry. Beneath it, a quieter line of type carries the article into the columns and leaves room for the surrounding page to breathe.",
    "The page follows a familiar newspaper rhythm: a title, a byline, a few lines of opening text, and a body set in narrow columns. The spacing helps separate each part.",
    "Each passage on this page is sample copy for an editorial layout. No verified quotation, event or statistic is being reported, and no real publisher or author is represented.",
    "A thin rule makes a clear break between sections. The columns continue below it, keeping their line length short and their type consistent.",
    "Readers move through the page by following changes in size and weight. A strong headline, a softer introduction and steady body text work together as one printed composition.",
    "Careful line spacing makes the page easier to read while keeping the type close enough to feel like newsprint. Each column continues below the lead and ends at a natural break.",
    "This sample uses an editorial tone and a traditional serif face, with a little grain in the paper. It was composed as a visual mock-up, not copied from a published report.",
    "Different editions vary the column width, font and order of their supporting notes. Small visual differences let each page turn without shifting the repeated phrase.",
    "The brief line near the top offers the subject in context, while the remaining copy carries the familiar cadence of a newspaper article. All surrounding material is invented for this demonstration.",
    "A boxed note gives the fictional page a new focal point without adding a real quotation or source.",
    "The sample headline sits above deliberately invented copy, with a narrow rule separating the sections.",
    "This mock edition uses a quiet opening line before the denser body columns begin.",
    "A small change in column width gives the imagined article a different reading pace.",
    "The page number and section label are decorative details created for this print-inspired layout.",
    "A short caption changes the balance of the fictional page while leaving its subject unnamed.",
    "Serif display type gives the invented headline a stronger voice than the smaller body copy.",
    "The wide outer margin leaves breathing room around the imaginary article text.",
    "A thin separator gives the reader a pause between the headline and the sample columns.",
    "The opening paragraph is set apart so the eye can move from the title into the body.",
    "The imagined copy desk has arranged the lines to make this edition feel distinct from the last.",
    "Short blocks of invented text create a different texture across the lower half of the page.",
    "A side note adds variety to the composition without pointing to a real person or event.",
    "This page uses a restrained headline and a little extra space around its opening sentence.",
    "The mock newspaper layout changes its visual rhythm through spacing, type size, and column order.",
    "A compact subheading gives the imaginary story a clear entrance into its columns.",
    "No real publisher is represented; the masthead and supporting details are part of the mock-up.",
    "The invented edition lets the page structure carry the motion while the phrase remains in focus.",
    "A second column begins lower on the page to create an intentionally varied print composition.",
    "The spacing around the sample text is adjusted to keep the paper-like layout easy to scan.",
    "An understated byline area changes the page balance but does not name a real author.",
    "The final lines are sample copy, written only to give the fictional page a full newspaper texture.",
    "A brief section marker and fine rule make this imaginary edition look different at a glance.",
    "The headline and body use different weights so the invented copy has a clear reading order.",
    "A small shift in the lead and margins gives this fictional page a fresh arrangement.",
    "The mock article uses no verified quotes, statistics, or reporting from a public source.",
    "This printed-page study keeps every supporting detail fictional and its setting unnamed.",
    "The columns are set as sample text, with the visual details changing across the sequence.",
    "A softer introduction sits above denser copy to create another imagined editorial rhythm.",
    "The page is composed from invented language and newspaper-style typography for this video.",
)


def _fictional_page(word: str, index: int) -> tuple[str, bytes, dict]:
    section_index = (index // len(_FICTIONAL_HEADLINES)) % len(_FICTIONAL_SECTIONS)
    detail_index = (index // len(_FICTIONAL_LEADS)) % len(_FICTIONAL_LEAD_DETAILS)
    headline = f"{_FICTIONAL_HEADLINES[index % len(_FICTIONAL_HEADLINES)]}: {_FICTIONAL_SECTIONS[section_index]}"
    lead = f"{_FICTIONAL_LEADS[index % len(_FICTIONAL_LEADS)]} {_FICTIONAL_LEAD_DETAILS[detail_index]}"
    paragraphs = [line.format(word=word) for line in _FICTIONAL_BODY[index % len(_FICTIONAL_BODY)]]
    copy_seed = int.from_bytes(
        hashlib.sha256(f"{word.casefold()}:{index}".encode("utf-8")).digest()[:8],
        "big",
    )
    copy_rng = random.Random(copy_seed)
    paragraphs.append(
        f"The {_FICTIONAL_LAYOUT_SUBJECTS[(index // len(_FICTIONAL_LAYOUT_STYLES)) % len(_FICTIONAL_LAYOUT_SUBJECTS)]} "
        f"sits within a {_FICTIONAL_LAYOUT_STYLES[index % len(_FICTIONAL_LAYOUT_STYLES)]} layout in this fictional edition."
    )
    paragraphs.extend(copy_rng.sample(_FICTIONAL_COLUMN_COPY, k=8))
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
        masthead="The Daily Edition",
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
        def ocr_progress(value: int, message: str) -> None:
            if progress:
                progress("03 — Running OCR", 31 + round(value * 0.12), message)

        analyze_articles(added, target_word, ocr_progress)
    matches = [article for article in added if article.found and article.bbox and article.confidence >= 0.30]
    matches = sorted(matches, key=_quality, reverse=True)
    matches = order_for_match_cuts(matches)
    kept_real = matches[:requested_count]
    kept_real_ids = {article.id for article in kept_real}
    for article in added:
        if article.id not in kept_real_ids:
            Path(article.path).unlink(missing_ok=True)
            Path(article.thumbnail_path).unlink(missing_ok=True)
            ARTICLES.pop(article.id, None)
    session.article_ids = [article.id for article in kept_real]
    matches = list(kept_real)

    if progress:
        progress("04 — Finding Target Word", 43, f"OCR confirmed {len(matches)} public article matches")
    fallback_count = requested_count - len(matches)
    if fallback_count:
        if progress:
            progress(
                "05 — Filling Missing Pages",
                48,
                f"Found {len(matches)} public pages; creating {fallback_count} labeled editorial page(s) to reach {requested_count}",
            )
        for offset in range(fallback_count):
            filename, payload, source = _fictional_page(target_word, len(matches) + offset)
            if not source.get("found") or not source.get("bbox"):
                raise RuntimeError("A generated page could not place the highlighted phrase.")
            article = add_upload(session.id, filename, payload, source)[0]
            article.found = True
            article.text = target_word
            article.confidence = float(source.get("confidence") or 1.0)
            article.bbox = list(source["bbox"])
            article.analysis_target = target_word
            session.article_ids.append(article.id)
            matches.append(article)

    selected = order_for_match_cuts(matches)[:requested_count]
    if len(selected) != requested_count:
        raise RuntimeError(f"Could not prepare the requested {requested_count} video pages.")
    session.article_ids = [article.id for article in selected]
    session.analysis_target = target_word
    if progress:
        public_count = sum(not bool((article.source or {}).get("generated")) for article in selected)
        generated_count = len(selected) - public_count
        progress(
            "06 — Building Match Cut Timeline",
            58,
            f"Prepared {public_count} public and {generated_count} generated page(s), {len(selected)} total",
        )
    return session, selected
