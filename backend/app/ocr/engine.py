from __future__ import annotations

import re
import random
import threading
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

_engine = None
_engine_lock = threading.Lock()


def normalize_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if char.isalnum())


def normalized_terms(value: str) -> list[str]:
    """Return normalized alphanumeric words while retaining phrase boundaries."""
    return [normalize_token(token) for token in re.findall(r"[^\W_]+", value, flags=re.UNICODE) if normalize_token(token)]


def _get_engine():
    global _engine
    if _engine is None:
        try:
            from rapidocr import RapidOCR
            _engine = RapidOCR(params={"Rec.lang_type": "en"})
        except ImportError as exc:
            raise RuntimeError("OCR dependencies are not installed. Run `pip install -r requirements.txt` in the project environment.") from exc
    return _engine


def _outputs(result):
    if hasattr(result, "boxes"):
        boxes = [] if result.boxes is None else list(result.boxes)
        texts = [] if result.txts is None else list(result.txts)
        scores = [] if result.scores is None else list(result.scores)
        word_results = [] if getattr(result, "word_results", None) is None else list(result.word_results)
        return boxes, texts, scores, word_results
    if isinstance(result, (tuple, list)) and len(result) >= 1:
        rows = result[0] if len(result) == 1 or result[0] is None else result
        if rows is None:
            return [], [], [], []
        boxes, texts, scores = [], [], []
        for row in rows:
            if not isinstance(row, (tuple, list)) or len(row) < 3:
                continue
            boxes.append(row[0])
            texts.append(row[1])
            scores.append(row[2])
        return boxes, texts, scores, []
    return [], [], [], []


def _box_from_polygon(polygon) -> tuple[float, float, float, float]:
    points = list(polygon)
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _word_box(line: str, token_match: re.Match, line_box: tuple[float, float, float, float]):
    x, y, width, height = line_box
    left_text = line[:token_match.start()]
    word_text = token_match.group(0)
    # RapidOCR returns text-line polygons. Divide that line polygon by character
    # advance so the highlight surrounds the matching word rather than a full row.
    def advance(value: str) -> float:
        return sum(0.48 if character.isspace() else 1.0 for character in value)

    total = max(advance(line), 1.0)
    left = advance(left_text) / total
    span = max(advance(word_text) / total, 0.035)
    pad = min(0.012, max(0.002, 0.012 / total))
    x0 = x + width * max(0.0, left - pad)
    x1 = x + width * min(1.0, left + span + pad)
    return [round(x0, 2), round(y, 2), round(max(1.0, x1 - x0), 2), round(height, 2)]


def _phrase_box(line: str, token_matches: list[re.Match], start: int, end: int, line_box: tuple[float, float, float, float], exact_boxes: list[tuple[str, tuple[float, float, float, float], float, str]]) -> tuple[list[float], float, str]:
    """Box a contiguous phrase, preferring RapidOCR word coordinates when available."""
    wanted = [normalize_token(token_matches[index].group(0)) for index in range(start, end + 1)]
    exact: list[tuple[float, float, float, float]] = []
    confidences: list[float] = []
    cursor = 0
    for term in wanted:
        found = next((index for index in range(cursor, len(exact_boxes)) if exact_boxes[index][0] == term), None)
        if found is None:
            exact = []
            break
        _, box, score, _ = exact_boxes[found]
        exact.append(box)
        confidences.append(score)
        cursor = found + 1
    if exact:
        x0 = min(box[0] for box in exact)
        y0 = min(box[1] for box in exact)
        x1 = max(box[0] + box[2] for box in exact)
        y1 = max(box[1] + box[3] for box in exact)
        confidence = sum(confidences) / len(confidences)
        matched_text = " ".join(token_matches[index].group(0) for index in range(start, end + 1))
        return [x0, y0, x1 - x0, y1 - y0], confidence, matched_text
    first = _word_box(line, token_matches[start], line_box)
    last = _word_box(line, token_matches[end], line_box)
    x0 = min(first[0], last[0])
    y0 = min(first[1], last[1])
    x1 = max(first[0] + first[2], last[0] + last[2])
    y1 = max(first[1] + first[3], last[1] + last[3])
    return [x0, y0, x1 - x0, y1 - y0], 0.0, " ".join(token_matches[index].group(0) for index in range(start, end + 1))


def _score(confidence: float, bbox: list[float], image_size: tuple[int, int]) -> float:
    width, height = image_size
    # Reward readable text while avoiding tiny folios and footer mentions.
    relative_height = bbox[3] / max(height, 1)
    size_score = min(relative_height / 0.035, 1.0)
    x_center = (bbox[0] + bbox[2] / 2) / max(width, 1)
    y_center = (bbox[1] + bbox[3] / 2) / max(height, 1)
    center_score = 1.0 - min(abs(x_center - 0.5) + abs(y_center - 0.48) * 0.5, 1.0)
    return 0.48 * confidence + 0.34 * size_score + 0.18 * center_score


def analyze_image(image_path: str | Path, target_word: str) -> dict:
    path = Path(image_path)
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        original_size = image.size
        # OCR works on a maximum 2000 px long edge; map boxes back to source pixels.
        image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
        ocr_scale_x = original_size[0] / image.width
        ocr_scale_y = original_size[1] / image.height
        import numpy as np
        ocr_image = np.asarray(image)
    with _engine_lock:
        output = _get_engine()(ocr_image, return_word_box=True)
    polygons, texts, scores, word_results = _outputs(output)
    match_candidates = []
    detected_text = []
    detections = []
    wanted = normalized_terms(target_word)
    for row_index, (polygon, text, confidence) in enumerate(zip(polygons, texts, scores)):
        text = str(text or "")
        if text:
            detected_text.append(text)
        confidence = float(confidence or 0.0)
        try:
            line_box = _box_from_polygon(polygon)
        except (TypeError, ValueError, IndexError):
            continue
        full_box = [round(line_box[0] * ocr_scale_x, 2), round(line_box[1] * ocr_scale_y, 2), round(line_box[2] * ocr_scale_x, 2), round(line_box[3] * ocr_scale_y, 2)]
        if len(detections) < 1500:
            detections.append({"text": text, "bbox": full_box, "confidence": max(0.0, min(1.0, confidence))})
        # Match complete Unicode tokens, and contiguous short phrases, never substrings.
        exact_boxes: list[tuple[str, tuple[float, float, float, float], float, str]] = []
        if row_index < len(word_results):
            for word_item in word_results[row_index] or []:
                if not isinstance(word_item, (tuple, list)) or len(word_item) < 3:
                    continue
                token_text, token_score, token_polygon = word_item[0], word_item[1], word_item[2]
                if token_polygon is None:
                    continue
                try:
                    exact_boxes.append((normalize_token(str(token_text)), _box_from_polygon(token_polygon), float(token_score or confidence), str(token_text)))
                except (TypeError, ValueError, IndexError):
                    pass
        tokens = list(re.finditer(r"[^\W_]+", text, flags=re.UNICODE))
        if not wanted or len(tokens) < len(wanted):
            continue
        for start in range(len(tokens) - len(wanted) + 1):
            end = start + len(wanted) - 1
            if [normalize_token(token.group(0)) for token in tokens[start:end + 1]] != wanted:
                continue
            box, exact_confidence, matched_text = _phrase_box(text, tokens, start, end, line_box, exact_boxes)
            box = [box[0] * ocr_scale_x, box[1] * ocr_scale_y, box[2] * ocr_scale_x, box[3] * ocr_scale_y]
            token_confidence = exact_confidence or confidence
            match_candidates.append({
                "bbox": [round(value, 2) for value in box],
                "confidence": max(0.0, min(1.0, token_confidence)),
                "text": matched_text,
                "score": _score(token_confidence, box, original_size),
            })
    match_candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    return {
        "detected_text": "\n".join(detected_text),
        "matches": match_candidates,
        "detections": detections,
        "selected": match_candidates[0] if match_candidates else None,
        "image_width": original_size[0],
        "image_height": original_size[1],
    }


def write_box_thumbnail(image_path: str | Path, thumbnail_path: str | Path, bbox: list[float] | None) -> None:
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((500, 620), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (500, 620), "#ded9cb")
        x_offset = (500 - image.width) // 2
        y_offset = (620 - image.height) // 2
        canvas.paste(image, (x_offset, y_offset))
        if bbox:
            scale = image.width / source.width
            rect = (
                x_offset + bbox[0] * scale,
                y_offset + bbox[1] * scale,
                x_offset + (bbox[0] + bbox[2]) * scale,
                y_offset + (bbox[1] + bbox[3]) * scale,
            )
            x1, y1, x2, y2 = rect
            stroke_height = max(3, (y2 - y1) * 0.76)
            stroke_width = max(7, (x2 - x1) * 1.10)
            stroke = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            marker = ImageDraw.Draw(stroke)
            rng = random.Random(913)
            top = []
            bottom = []
            for index in range(9):
                x = x1 - (stroke_width - (x2 - x1)) / 2 + stroke_width * index / 8
                top.append((x, y1 + (y2 - y1) * 0.12 + rng.uniform(-0.8, 0.8)))
                bottom.append((x, y1 + (y2 - y1) * 0.12 + stroke_height + rng.uniform(-0.8, 0.8)))
            marker.polygon(top + list(reversed(bottom)), fill=(246, 222, 73, 93))
            canvas = Image.alpha_composite(canvas.convert("RGBA"), stroke).convert("RGB")
        canvas.save(thumbnail_path, "JPEG", quality=84, optimize=True)
