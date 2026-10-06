from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw

from ..models import Article
from .acquisition import _font, _page_font_style
from .storage import _new_article, create_session

_PAPERS = ["THE DAILY RECORD", "THE EVENING JOURNAL", "THE NATIONAL REVIEW", "MORNING EDITION", "THE WEEKLY OBSERVER", "THE CITY LEDGER"]
_HEADLINES = [
    "NASA maps its next chapter beyond Earth",
    "A new NASA mission looks toward the Moon",
    "NASA prepares a closer look at the red planet",
    "Scientists say NASA's latest signal is promising",
    "NASA returns to the front page this morning",
    "The long road ahead for NASA's next crew",
]
_BODY = [
    "A carefully planned mission is opening a new window on the questions that have occupied researchers for generations. Engineers say each stage is designed to gather clearer evidence and leave room for the next discovery.",
    "The report follows months of preparation and a series of tests across several research centers. The findings are expected to shape the work of scientists, mission planners and the communities who follow the program.",
    "New instruments will carry the work forward, building on years of observations and a growing archive of public research. The team says a measured approach remains essential as the schedule develops.",
]


def _generate_page(index: int, path: Path) -> None:
    rng = random.Random(9327 + index)
    family_index, font_style = _page_font_style(index)
    image = Image.new("RGB", (1200, 1700), "#f1eee5")
    draw = ImageDraw.Draw(image)
    ink = "#24231f"
    draw.rectangle((48, 48, 1152, 1652), outline=ink, width=3)
    draw.text((84, 77), _PAPERS[index], font=_font(44, bold=True, family_index=family_index), fill=ink)
    draw.text((88, 143), f"VOL. {index + 1:02d}     •     SPECIAL SCIENCE EDITION", font=_font(18, family_index=family_index), fill="#55534b")
    draw.text((824, 143), f"OCTOBER {7 + index}, 2026", font=_font(18, family_index=family_index), fill="#55534b")
    draw.line((80, 184, 1120, 184), fill=ink, width=3)
    draw.text((86, 212), "SCIENCE  /  EXPLORATION", font=_font(20, family_index=family_index), fill="#777267")
    headline_font = _font(110, bold=True, family_index=family_index)
    headline = _HEADLINES[index]
    words = headline.split()
    lines, line = [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=headline_font)[2] > 1000 and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    y = 266
    for line in lines:
        draw.text((86, y), line, font=headline_font, fill=ink, stroke_width=0)
        y += 125
    draw.line((84, y + 12, 1116, y + 12), fill="#777267", width=2)
    draw.text((88, y + 36), "REPORTING DESK     |     By the Science Correspondent", font=_font(20, family_index=family_index), fill="#666157")
    y += 93
    body_font = _font(31, family_index=family_index)
    paragraph = _BODY[index % len(_BODY)]
    body_words = (paragraph + " " + paragraph + " " + paragraph).split()
    column_width = 478
    for column in range(2):
        x, col_y = 88 + column * 542, y
        col_words = body_words[column * 36:(column + 1) * 36]
        lines, line = [], ""
        for word in col_words:
            candidate = f"{line} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=body_font)[2] > column_width and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        for body_line in lines:
            draw.text((x, col_y), body_line, font=body_font, fill="#35332d")
            col_y += 47
        for rule_y in range(col_y + 20, 1540, 19):
            draw.line((x, rule_y, x + rng.randint(400, 470), rule_y), fill="#b9b4a8", width=1)
    draw.line((84, 1572, 1116, 1572), fill=ink, width=2)
    draw.text((88, 1588), f"LOCAL SAMPLE LAYOUT     •     PAGE {index + 1}", font=_font(17, family_index=family_index), fill="#777267")
    image.save(path, "JPEG", quality=94)


def create_demo() -> tuple[str, list[Article]]:
    session = create_session()
    session.target_word = "NASA"
    articles = []
    for index in range(6):
        path = Path(session.id + "-demo.jpg")
        from ..config import WORK_DIR
        path = WORK_DIR / path
        _generate_page(index, path)
        article = _new_article(
            session.id,
            f"Sample layout {index + 1:02d}.jpg",
            path.read_bytes(),
            1,
            {
                "publication": "LOCAL DEMO LAYOUT",
                "headline": _HEADLINES[index],
                "date": "2026-10",
                "source_url": "",
                "generated": True,
                "font_style": _page_font_style(index)[1],
            },
        )
        session.article_ids.append(article.id)
        articles.append(article)
        path.unlink(missing_ok=True)
    return session.id, articles
