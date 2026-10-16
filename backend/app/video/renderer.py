from __future__ import annotations

import gc
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from math import gcd, pi, sin
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .. import config
from ..audio.soundscape import SFX_ASSETS, build_audio, canonical_sound_style
from ..models import Article

PLAYBACK_SPEED = 2.0
SUPPORTED_IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}
logger = logging.getLogger("matchcut.render")


def _rss_mb() -> str:
    try:
        with open("/proc/self/status", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return f"{int(line.split()[1]) / 1024:.0f}MB"
    except OSError:
        pass
    return "n/a"


def ffmpeg_path() -> str:
    if config.FFMPEG_BINARY:
        if Path(config.FFMPEG_BINARY).is_file():
            return config.FFMPEG_BINARY
        raise RuntimeError(
            "FFmpeg is missing. FFMPEG_BINARY does not point to an executable."
        )

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if os.name != "nt":
            os.chmod(path, os.stat(path).st_mode | 0o111)
        return path
    except Exception as exc:
        raise RuntimeError(
            "FFmpeg is unavailable. Install FFmpeg or set FFMPEG_BINARY to an FFmpeg executable."
        ) from exc


def validate_articles(articles: list[Article]) -> None:
    if len(articles) < 2:
        raise ValueError(
            "At least 2 OCR-confirmed article pages are required to render a match cut."
        )

    for article in articles:
        path = Path(article.path or "")

        if not path.is_file():
            raise ValueError(
                f"{article.filename}: the article image is missing from storage."
            )

        if path.stat().st_size < 64:
            raise ValueError(
                f"{article.filename}: the article image is empty."
            )

        if path.suffix.lower() not in SUPPORTED_IMAGE_FORMATS:
            raise ValueError(
                f"{article.filename}: unsupported image format. Use JPG, PNG, or WEBP."
            )

        if not article.bbox or len(article.bbox) != 4:
            raise ValueError(
                f"{article.filename}: the target-word box is missing or invalid."
            )

        try:
            _x, _y, box_width, box_height = [
                float(value) for value in article.bbox
            ]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{article.filename}: the target-word box is invalid."
            ) from exc

        if box_width < 2 or box_height < 2:
            raise ValueError(
                f"{article.filename}: the target-word box is too small to render."
            )

        if (
            article.image_width
            and article.image_height
            and (
                article.image_width < 2
                or article.image_height < 2
            )
        ):
            raise ValueError(
                f"{article.filename}: the article dimensions are invalid."
            )


def _cheap_blur(image: Image.Image, radius: float) -> Image.Image:
    if radius <= 0:
        return image

    scale = max(
        1,
        min(12, int(radius / 3.5) or 1),
    )

    small = image.resize(
        (
            max(1, image.width // scale),
            max(1, image.height // scale),
        ),
        Image.Resampling.BILINEAR,
    )

    softened = None
    try:
        softened = small.filter(ImageFilter.GaussianBlur(radius=max(0.6, radius / scale)))
        return softened.resize(image.size, Image.Resampling.BILINEAR)
    finally:
        small.close()
        if softened is not None:
            softened.close()


def _prepare_source(
    article: Article,
    width: int,
    height: int,
):
    if not article.bbox:
        raise ValueError(
            f"{article.filename}: no target word box is available."
        )

    max_edge = max(width, height) * 2
    with Image.open(article.path) as image:
        oriented = ImageOps.exif_transpose(image)
        original_width, original_height = oriented.size
        oriented.thumbnail((max_edge, max_edge), Image.Resampling.BILINEAR)
        source_work = oriented.copy()
        if oriented is not image:
            oriented.close()

    bbox = [
        float(value)
        for value in article.bbox
    ]

    scale_x = source_work.width / max(original_width, 1)
    scale_y = source_work.height / max(original_height, 1)
    bbox = [bbox[0] * scale_x, bbox[1] * scale_y, bbox[2] * scale_x, bbox[3] * scale_y]
    source = source_work.convert("RGB")
    source_work.close()

    bbox[0] = max(
        0.0,
        min(bbox[0], source.width - 1.0),
    )

    bbox[1] = max(
        0.0,
        min(bbox[1], source.height - 1.0),
    )

    bbox[2] = max(
        2.0,
        min(bbox[2], source.width - bbox[0]),
    )

    bbox[3] = max(
        2.0,
        min(bbox[3], source.height - bbox[1]),
    )

    fitted = ImageOps.fit(
        source,
        (width, height),
        method=Image.Resampling.BILINEAR,
        centering=(
            (bbox[0] + bbox[2] / 2) / source.width,
            (bbox[1] + bbox[3] / 2) / source.height,
        ),
    )

    blurred = _cheap_blur(fitted, 42)
    fitted.close()
    background = ImageEnhance.Brightness(blurred).enhance(0.96)
    blurred.close()

    return source, bbox, background


def _draw_marker(
    frame: Image.Image,
    box: tuple[float, float, float, float],
    reveal_fraction: float = 1.0,
) -> Image.Image:
    """Draw a lightly textured hand-marked stroke while keeping printed ink readable."""
    reveal_fraction = max(0.0, min(1.0, reveal_fraction))
    if reveal_fraction <= 0.0:
        return frame

    x1, y1, x2, y2 = box
    word_width = max(2.0, x2 - x1)
    word_height = max(2.0, y2 - y1)

    pad_x = max(10, round(word_width * 0.055))
    stroke_height = max(10, round(word_height * 0.82))
    left = max(0, round(x1 - pad_x))
    top = max(0, round(y1 + word_height * 0.09))
    right = min(frame.width, round(x1 - pad_x + (word_width + 2 * pad_x) * reveal_fraction))
    bottom = min(frame.height, top + stroke_height)

    patch_width = right - left
    patch_height = bottom - top

    if patch_width < 2 or patch_height < 2:
        return frame

    mask = Image.new("L", (patch_width, patch_height), 0)
    rng = np.random.default_rng(8197)
    brush = ImageDraw.Draw(mask)
    knots = max(8, min(22, round(patch_width / 28)))
    upper = []
    lower = []
    for index in range(knots + 1):
        x = index * max(0, patch_width - 1) / knots
        tilt = (index / knots - 0.5) * patch_height * 0.05
        top_edge = patch_height * (0.16 + rng.uniform(-0.055, 0.055)) + tilt
        bottom_edge = patch_height * (0.83 + rng.uniform(-0.06, 0.06)) + tilt
        upper.append((x, max(0, min(patch_height - 1, top_edge))))
        lower.append((x, max(0, min(patch_height - 1, bottom_edge))))
    brush.polygon(upper + list(reversed(lower)), fill=255)
    softened_mask = mask.filter(ImageFilter.GaussianBlur(radius=1.0))
    mask.close()
    original = frame.crop((left, top, right, bottom))
    color = Image.new("RGB", (patch_width, patch_height), (255, 226, 24))
    alpha_image = None
    marker = None
    try:
        mask_values = np.asarray(softened_mask, dtype=np.float32)
        row_texture = rng.uniform(0.88, 1.04, size=(patch_height, 1)).astype(np.float32)
        texture = rng.uniform(0.82, 1.0, size=mask_values.shape).astype(np.float32) * row_texture
        pixels = np.asarray(original, dtype=np.float32)
        luminance = pixels[..., 0] * 0.2126 + pixels[..., 1] * 0.7152 + pixels[..., 2] * 0.0722
        ink_visibility = 0.42 + 0.58 * np.clip(luminance / 220.0, 0.0, 1.0)
        alpha = np.clip(mask_values * texture * 0.88 * ink_visibility, 0, 255).astype(np.uint8)
        alpha_image = Image.fromarray(alpha, mode="L")
        marker = Image.composite(color, original, alpha_image)
        frame.paste(marker, (left, top))
        del mask_values, row_texture, texture, pixels, luminance, ink_visibility, alpha
    finally:
        softened_mask.close()
        original.close()
        color.close()
        if alpha_image is not None:
            alpha_image.close()
        if marker is not None:
            marker.close()
    return frame


def _draw_underline(
    frame: Image.Image,
    box: tuple[float, float, float, float],
    reveal_fraction: float,
) -> Image.Image:
    reveal_fraction = max(
        0.0,
        min(1.0, reveal_fraction),
    )

    if reveal_fraction <= 0.0:
        return frame

    x1, y1, x2, y2 = box

    line_start = round(
        x1 - (x2 - x1) * 0.025
    )

    line_end = round(
        x1 + (x2 - x1) * reveal_fraction
    )

    line_y = round(
        y2 + max(3, (y2 - y1) * 0.1)
    )

    line_width = max(
        4,
        round((y2 - y1) * 0.09),
    )

    draw = ImageDraw.Draw(
        frame,
        "RGBA",
    )

    draw.line(
        (
            line_start,
            line_y,
            line_end,
            line_y,
        ),
        fill=(222, 190, 20, 225),
        width=line_width,
    )

    return frame


def _focus_falloff(
    frame: Image.Image,
    box: tuple[float, float, float, float],
    blur_intensity: float = 1.0,
) -> Image.Image:
    """Keep the phrase and nearby article lines sharp; soften the outer crop."""
    x1, y1, x2, y2 = box

    margin_x = max(
        round(frame.width * 0.38),
        round((x2 - x1) * 1.2),
    )
    margin_y = max(
        round(frame.height * 0.28),
        round((y2 - y1) * 2.2),
    )

    core = (
        max(0, round(x1 - margin_x)),
        max(0, round(y1 - margin_y)),
        min(frame.width, round(x2 + margin_x)),
        min(frame.height, round(y2 + margin_y)),
    )

    mask = Image.new(
        "L",
        frame.size,
        0,
    )

    ImageDraw.Draw(mask).rounded_rectangle(
        core,
        radius=max(36, margin_y // 2),
        fill=255,
    )

    softened_mask = _cheap_blur(mask, max(48, round(frame.height * 0.045)))
    mask.close()
    softened = _cheap_blur(frame, 4.6 * max(0.0, blur_intensity))
    try:
        return Image.composite(frame, softened, softened_mask)
    finally:
        softened.close()
        softened_mask.close()


def _compose_page_frame(
    article: Article,
    width: int,
    height: int,
    target_box: tuple[float, float],
    highlight_mode: str,
    caption: str,
    blur_intensity: float = 1.0,
) -> Image.Image:
    source, bbox, background = _prepare_source(
        article,
        width,
        height,
    )
    frame = None
    try:
        reveal = (
            1.0
            if highlight_mode == "default"
            else 0.0
        )

        frame = _make_frame(
            source,
            bbox,
            background,
            width,
            height,
            target_box,
            highlight_mode,
            reveal,
            blur_intensity=blur_intensity,
        )

        result = _draw_caption(
            frame,
            caption,
        )
        frame = None
        return result

    finally:
        source.close()
        background.close()
        if frame is not None:
            frame.close()


def _make_frame(
    source: Image.Image,
    bbox: list[float],
    background: Image.Image,
    width: int,
    height: int,
    target_box: tuple[float, float],
    highlight_mode: str = "highlight",
    reveal_fraction: float = 1.0,
    blur_intensity: float = 1.0,
) -> Image.Image:
    word_cx = bbox[0] + bbox[2] / 2
    word_cy = bbox[1] + bbox[3] / 2

    target_width, target_height = target_box

    scale_x = target_width / max(
        bbox[2],
        1.0,
    )

    scale_y = target_height / max(
        bbox[3],
        1.0,
    )

    # Compute source region offsets to center the target word. No clamping; out‑of‑bounds area will be filled with black.
    left = word_cx - width / (2 * scale_x)
    top = word_cy - height / (2 * scale_y)
    # Off‑screen offsets are allowed; PIL will fill with the background color via fillcolor.
    affine = (
        1 / scale_x,
        0,
        left,
        0,
        1 / scale_y,
        top,
    )

    visible = None
    source_mask = None
    frame = None
    shadow_mask = None
    shadow_layer = None
    inverted_shadow = None
    paper_tone = None
    try:
        visible = source.transform(
            (width, height), Image.Transform.AFFINE, affine,
            resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0),
        )
        base_mask = Image.new("L", source.size, 255)
        try:
            source_mask = base_mask.transform(
                (width, height), Image.Transform.AFFINE, affine,
                resample=Image.Resampling.BICUBIC, fillcolor=0,
            )
        finally:
            base_mask.close()

        frame = background.copy()
        # Subtle drop shadow behind the source page.
        shadow_mask = source_mask.filter(ImageFilter.GaussianBlur(radius=5.0))
        shadow_layer = Image.new("RGB", (width, height), (30, 28, 24))
        inverted_shadow = ImageOps.invert(shadow_mask)
        composited = Image.composite(frame, shadow_layer, inverted_shadow)
        frame.close()
        frame = composited
        frame.paste(visible, (0, 0), source_mask)

        screen_box = (
            (width - target_width) / 2,
            (height - target_height) / 2,
            (width + target_width) / 2,
            (height + target_height) / 2,
        )

        paper_tone = Image.new("RGB", frame.size, (247, 243, 231))
        blended = Image.blend(frame, paper_tone, 0.045)
        frame.close()
        frame = blended
        focused = _focus_falloff(frame, screen_box, blur_intensity)
        frame.close()
        frame = focused

        if highlight_mode in {"default", "highlight"}:
            result = _draw_marker(frame, screen_box, 1.0 if highlight_mode == "default" else reveal_fraction)
        elif highlight_mode == "underline":
            result = _draw_underline(frame, screen_box, reveal_fraction)
        else:
            raise ValueError(f"Unsupported highlight mode: {highlight_mode}")
        frame = None  # The caller owns the returned image from this point.
        return result
    finally:
        for image in (visible, source_mask, shadow_mask, shadow_layer, inverted_shadow, paper_tone, frame):
            if image is not None:
                image.close()


def _zoom_frame(
    frame: Image.Image,
    scale: float,
    shake_x: float = 0.0,
    shake_y: float = 0.0,
) -> Image.Image:
    if scale <= 1.0:
        return frame

    width, height = frame.size

    inverse_scale = 1.0 / scale

    offset_x = (
        width
        - width * inverse_scale
    ) / 2 - shake_x * inverse_scale

    offset_y = (
        height
        - height * inverse_scale
    ) / 2 - shake_y * inverse_scale

    return frame.transform(
        frame.size,
        Image.Transform.AFFINE,
        (
            inverse_scale,
            0,
            offset_x,
            0,
            inverse_scale,
            offset_y,
        ),
        resample=Image.Resampling.BICUBIC,
    )


def _timeline_progress(
    frame_index: int,
    total_frames: int,
) -> float:
    return frame_index / max(
        1,
        total_frames - 1,
    )


def _target_box_for_page(
    bbox: list[float],
    target_word: str,
    width: int,
    height: int,
    page_index: int,
    zoom_level: float = 1.0,
) -> tuple[float, float]:
    scale_beats = (
        0.92,
        1.08,
        0.88,
        1.14,
        0.96,
        1.04,
    )

    base_width = width * min(
        0.50,
        max(
            0.25,
            0.25 + len(target_word.strip()) * 0.012,
        ),
    )

    target_width = min(
        width * 0.84,
        max(
            width * 0.24,
            base_width
            * scale_beats[
                page_index % len(scale_beats)
            ]
            * zoom_level,
        ),
    )

    target_height = (
        target_width
        * max(bbox[3], 1.0)
        / max(bbox[2], 1.0)
    )

    if target_height > height * 0.19:
        target_width *= (
            height * 0.19
            / target_height
        )

        target_height = height * 0.19

    return target_width, target_height


def _draw_caption(
    frame: Image.Image,
    caption: str,
) -> Image.Image:
    if not caption.strip():
        return frame

    font_size = max(
        18,
        round(min(frame.width, frame.height) * 0.026),
    )

    font_path = None

    for path in (
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ):
        try:
            ImageFont.truetype(
                path,
                font_size,
            )
            font_path = path
            break
        except OSError:
            continue

    text = " ".join(
        caption.strip().split()
    )[:80].upper()

    draw = ImageDraw.Draw(
        frame,
        "RGBA",
    )

    margin_x = round(
        frame.width * 0.067
    )

    margin_y = round(
        frame.height * 0.04
    )

    max_text_width = (
        frame.width
        - margin_x * 2
        - round(font_size * 1.6)
    )

    font = (
        ImageFont.truetype(
            font_path,
            font_size,
        )
        if font_path
        else ImageFont.load_default()
    )

    while (
        font_path
        and draw.textbbox(
            (0, 0),
            text,
            font=font,
        )[2]
        > max_text_width
        and font_size > 12
    ):
        font_size -= 2

        font = ImageFont.truetype(
            font_path,
            font_size,
        )

    padding_x = max(
        10,
        round(font_size * 0.8),
    )

    padding_y = max(
        8,
        round(font_size * 0.55),
    )

    left, bottom = (
        margin_x,
        frame.height - margin_y,
    )

    text_top = (
        bottom
        - font_size
        - padding_y
    )

    bounds = draw.textbbox(
        (left, text_top),
        text,
        font=font,
    )

    right = min(
        frame.width - margin_x,
        bounds[2] + padding_x,
    )

    draw.rounded_rectangle(
        (
            left - round(padding_x * 0.65),
            text_top - padding_y,
            right,
            bottom,
        ),
        radius=max(
            5,
            round(font_size * 0.2),
        ),
        fill=(13, 14, 12, 150),
    )

    draw.text(
        (left, text_top),
        text,
        font=font,
        fill=(241, 237, 224, 245),
    )

    return frame


def _write_rgb_frame(
    page_frame: Image.Image,
    frame_sink,
    width: int,
    height: int,
    screen_box: tuple[float, float, float, float],
    highlight_mode: str,
    reveal_fraction: float,
    camera_scale: float,
    shake_x: float,
    shake_y: float,
) -> None:
    """Write one RGB24 frame and release its full-size buffers immediately."""
    frame = page_frame.copy()
    raw_frame = None
    try:
        if highlight_mode == "highlight":
            _draw_marker(frame, screen_box, reveal_fraction)
        elif highlight_mode == "underline":
            _draw_underline(frame, screen_box, reveal_fraction)
        zoomed = _zoom_frame(frame, camera_scale, shake_x, shake_y)
        if zoomed is not frame:
            frame.close()
            frame = zoomed
        raw_frame = frame.tobytes("raw", "RGB")
        expected_bytes = width * height * 3
        if len(raw_frame) != expected_bytes:
            raise RuntimeError(f"Invalid raw frame size: got {len(raw_frame)} bytes, expected {expected_bytes} bytes for {width}x{height} RGB24.")
        frame_sink.write(raw_frame)
    finally:
        raw_frame = None
        frame.close()


def _sound_style(
    requested: str,
    articles: list[Article],
) -> str:
    style = (
        requested or "automatic"
    ).strip().lower()

    if style != "automatic":
        return canonical_sound_style(style)

    generated = sum(
        1
        for article in articles
        if (article.source or {}).get("generated")
    )

    if generated >= max(
        1,
        len(articles) // 2,
    ):
        return "paper"

    if len(articles) <= 3:
        return "minimal"

    return canonical_sound_style(
        "documentary"
    )


def _drain_stderr(
    stream,
    chunks: list[bytes],
) -> None:
    try:
        while True:
            piece = stream.read(4096)

            if not piece:
                break

            if sum(
                len(item)
                for item in chunks
            ) < 8000:
                chunks.append(piece)

    except OSError:
        return


def render_video(
    articles: list[Article],
    output_path: str | Path,
    duration: float = 10.0,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    sfx_enabled: bool = True,
    background_enabled: bool = True,
    sfx_volume: int = 72,
    background_volume: int = 32,
    caption: str = "",
    sound_style: str = "automatic",
    target_word: str = "",
    progress: Callable[[int, str], None] | None = None,
    sfx_id: str = "click",
    aspect_ratio: str | None = None,
    highlight_mode: str = "highlight",
    zoom_level: float = 1.5,
    blur_intensity: float = 1.0,
) -> dict:

    validate_articles(articles)

    if width % 2 or height % 2:
        raise ValueError(
            "The output width and height must be even numbers for H.264 encoding."
        )

    if sfx_id != "none" and sfx_id not in SFX_ASSETS:
        raise ValueError(
            f"Unsupported sound-effect ID: {sfx_id}"
        )

    divisor = gcd(
        width,
        height,
    )

    actual_aspect_ratio = (
        f"{width // divisor}:{height // divisor}"
    )

    if (
        aspect_ratio
        and aspect_ratio != actual_aspect_ratio
    ):
        raise ValueError(
            f"Aspect ratio {aspect_ratio} does not match the requested "
            f"{width}x{height} output dimensions."
        )

    if not 3.0 <= duration <= 30.0:
        raise ValueError(
            "Duration must be between 3 and 30 seconds."
        )

    if highlight_mode not in {
        "default",
        "highlight",
        "underline",
    }:
        raise ValueError(
            f"Unsupported highlight mode: {highlight_mode}"
        )

    if not 1.0 <= zoom_level <= 2.0:
        raise ValueError("Zoom level must be between 1.0× and 2.0×.")

    if not 0.0 <= blur_intensity <= 2.5:
        raise ValueError("Blur intensity must be between 0× and 2.5×.")

    fps = max(
        24,
        min(int(fps), 60),
    )

    output_duration = (
        duration / PLAYBACK_SPEED
    )

    total_frames = round(
        output_duration * fps
    )

    logger.info(
        "render start articles=%s size=%sx%s fps=%s frames=%s duration=%s rss=%s",
        len(articles),
        width,
        height,
        fps,
        total_frames,
        duration,
        _rss_mb(),
    )

    boundaries = [
        round(
            index * total_frames / len(articles)
        )
        for index in range(
            len(articles) + 1
        )
    ]

    cut_frames = boundaries[1:-1]

    selected_style = _sound_style(
        sound_style,
        articles,
    )

    # Compute per-page target boxes (width, height) for each article
    target_boxes = [
        _target_box_for_page(
            [float(value) for value in article.bbox],
            target_word,
            width,
            height,
            index,
            zoom_level,
        )
        for index, article in enumerate(articles)
    ]

    # Use a common target box (max width & height) so the highlighted word stays centered consistently across all pages
    max_width = max(tb[0] for tb in target_boxes)
    max_height = max(tb[1] for tb in target_boxes)
    common_target_box = (max_width, max_height)
    target_boxes = [common_target_box for _ in articles]


    output_path = Path(output_path)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    work_dir = Path(
        config.WORK_DIR
    )

    work_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    with tempfile.TemporaryDirectory(
        prefix="matchcut-",
        dir=work_dir,
    ) as temp_dir:

        audio_path = (
            Path(temp_dir)
            / "sound-design.wav"
        )

        if progress:
            progress(
                0,
                "Building the frame-locked sound timeline",
            )

        logger.info(
            "audio start rss=%s",
            _rss_mb(),
        )

        build_audio(
            audio_path,
            output_duration,
            fps,
            cut_frames,
            sfx_enabled,
            background_enabled,
            sfx_volume,
            background_volume,
            selected_style,
            sfx_id=sfx_id,
        )

        logger.info(
            "audio done bytes=%s rss=%s",
            audio_path.stat().st_size,
            _rss_mb(),
        )

        encoder = ffmpeg_path()

        command = [
            encoder,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-frames:v",
            str(total_frames),
            "-t",
            f"{output_duration:.4f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "1",
            "-x264-params",
            "threads=1:sliced-threads=0",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        logger.info(
            "ffmpeg start cmd=%s rss=%s",
            " ".join(command),
            _rss_mb(),
        )

        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=1024 * 1024,
        )

        stderr_chunks: list[bytes] = []

        stderr_thread = threading.Thread(
            target=_drain_stderr,
            args=(
                process.stderr,
                stderr_chunks,
            ),
            daemon=True,
        )

        stderr_thread.start()

        page_frame = None
        loaded_index = -1

        deadline = (
            time.monotonic()
            + config.RENDER_TIMEOUT
        )

        try:
            assert process.stdin is not None

            article_index = 0

            for frame_index in range(
                total_frames
            ):

                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Video rendering timed out after "
                        f"{int(config.RENDER_TIMEOUT)} seconds."
                    )

                if process.poll() is not None:
                    message = (
                        b"".join(
                            stderr_chunks
                        )
                        .decode(
                            "utf-8",
                            errors="replace",
                        )
                        .strip()
                    )

                    raise RuntimeError(
                        "FFmpeg stopped while frames were still being written. "
                        f"{message[:700]}".strip()
                    )

                while (
                    article_index + 1
                    < len(articles)
                    and frame_index
                    >= boundaries[
                        article_index + 1
                    ]
                ):
                    article_index += 1

                if article_index != loaded_index:

                    if page_frame is not None:
                        page_frame.close()
                        page_frame = None
                        gc.collect()

                    logger.info(
                        "page %s/%s file=%s dims=%sx%s rss=%s",
                        article_index + 1,
                        len(articles),
                        articles[
                            article_index
                        ].filename,
                        articles[
                            article_index
                        ].image_width,
                        articles[
                            article_index
                        ].image_height,
                        _rss_mb(),
                    )

                    if progress:
                        progress(
                            round(
                                article_index
                                / max(
                                    len(articles),
                                    1,
                                )
                                * 100
                            ),
                            f"Preparing page "
                            f"{article_index + 1} of "
                            f"{len(articles)}",
                        )

                    page_frame = _compose_page_frame(
                        articles[
                            article_index
                        ],
                        width,
                        height,
                        target_boxes[
                            article_index
                        ],
                        highlight_mode,
                        caption,
                        blur_intensity=blur_intensity,
                    )

                    loaded_index = article_index

                page_start = boundaries[
                    article_index
                ]

                page_end = boundaries[
                    article_index + 1
                ]

                page_progress = (
                    frame_index
                    - page_start
                ) / max(
                    1,
                    page_end
                    - page_start
                    - 1,
                )

                timeline_progress = _timeline_progress(
                    frame_index,
                    total_frames,
                )

                target_box = target_boxes[
                    article_index
                ]

                screen_box = (
                    (width - target_box[0]) / 2,
                    (height - target_box[1]) / 2,
                    (width + target_box[0]) / 2,
                    (height + target_box[1]) / 2,
                )

                if highlight_mode == "default":
                    page_t = max(0.0, min(1.0, page_progress))
                    eased_progress = page_t * page_t * (3.0 - 2.0 * page_t)
                    camera_scale = 1.025 + 0.105 * eased_progress + 0.012 * sin(pi * page_t)
                    shake_x = width * (
                        0.0028 * sin(frame_index * 0.91)
                        + 0.0012 * sin(frame_index * 1.87)
                    )
                    shake_y = height * (
                        0.0022 * sin(frame_index * 0.77 + 0.6)
                        + 0.0010 * sin(frame_index * 1.63)
                    )
                else:
                    camera_scale = 1.0 + 0.045 * page_progress
                    shake_x = 0.0
                    shake_y = 0.0

                _write_rgb_frame(
                    page_frame,
                    process.stdin,
                    width,
                    height,
                    screen_box,
                    highlight_mode,
                    timeline_progress,
                    camera_scale,
                    shake_x,
                    shake_y,
                )

                if progress and (
                    frame_index
                    % max(1, fps // 2)
                    == 0
                    or frame_index + 1
                    == total_frames
                ):
                    progress(
                        round(
                            (frame_index + 1)
                            / total_frames
                            * 100
                        ),
                        f"Rendered frame "
                        f"{frame_index + 1} of "
                        f"{total_frames}",
                    )

            process.stdin.close()

            return_code = process.wait(
                timeout=max(
                    15.0,
                    config.RENDER_TIMEOUT / 6,
                )
            )

        except BaseException:

            if process.poll() is None:
                process.kill()

            try:
                process.wait(
                    timeout=8
                )
            except subprocess.TimeoutExpired:
                process.kill()

            output_path.unlink(
                missing_ok=True
            )

            raise

        finally:

            if page_frame is not None:
                page_frame.close()

            if process.stdin and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass

            stderr_thread.join(
                timeout=5
            )

            if process.stderr:
                process.stderr.close()
            gc.collect()

        stderr = (
            b"".join(
                stderr_chunks
            )
            .decode(
                "utf-8",
                errors="replace",
            )
            .strip()
        )

        logger.info(
            "ffmpeg exit code=%s stderr=%s rss=%s",
            return_code,
            stderr[:500],
            _rss_mb(),
        )

        if return_code != 0:

            output_path.unlink(
                missing_ok=True
            )

            detail = (
                stderr[:700]
                or "FFmpeg exited without writing an error message."
            )

            raise RuntimeError(
                "FFmpeg could not render the video. "
                f"{detail}"
            )

    if (
        not output_path.is_file()
        or output_path.stat().st_size < 1_024
    ):
        output_path.unlink(
            missing_ok=True
        )

        raise RuntimeError(
            "FFmpeg completed but the MP4 is missing or empty."
        )

    logger.info(
        "mp4 ready path=%s bytes=%s rss=%s",
        output_path,
        output_path.stat().st_size,
        _rss_mb(),
    )

    return {
        "width": width,
        "height": height,
        "aspect_ratio": actual_aspect_ratio,
        "fps": fps,
        "duration": round(
            total_frames / fps,
            3,
        ),
        "requested_duration": duration,
        "playback_speed": PLAYBACK_SPEED,
        "frame_count": total_frames,
        "article_count": len(articles),
        "cut_frames": cut_frames,
        "cut_times": [
            round(
                frame / fps,
                4,
            )
            for frame in cut_frames
        ],
        "audio_sample_rate": 48000,
        "audio_cut_frames": cut_frames,
        "codec": "H.264 / AAC",
        "sound_style": selected_style,
        "sfx_id": sfx_id,
        "sfx_asset": (
            SFX_ASSETS.get(sfx_id)
            if sfx_id != "none"
            else None
        ),
        "background_enabled": background_enabled,
        "background_volume": background_volume,
        "sfx_volume": sfx_volume,
        "highlight_mode": highlight_mode,
        "zoom_level": round(zoom_level, 2),
        "blur_intensity": round(blur_intensity, 2),
        "highlight": (
            "organic yellow pigment stroke shown at full strength for the default highlight"
            if highlight_mode == "default"
            else (
                "organic yellow pigment stroke progressively revealed across the target"
                if highlight_mode == "highlight"
                else "yellow underline progressively drawn beneath the target"
            )
        ),
        "target_lock": (
            "centered phrase with a restrained scale rhythm across source images"
        ),
        "target_screen_bbox": [
            round(
                (width - target_boxes[0][0]) / 2,
                2,
            ),
            round(
                (height - target_boxes[0][1]) / 2,
                2,
            ),
            round(
                target_boxes[0][0],
                2,
            ),
            round(
                target_boxes[0][1],
                2,
            ),
        ],
        "target_box_sequence": [
            {
                "x": round(
                    (width - box_width) / 2,
                    2,
                ),
                "y": round(
                    (height - box_height) / 2,
                    2,
                ),
                "width": round(
                    box_width,
                    2,
                ),
                "height": round(
                    box_height,
                    2,
                ),
            }
            for box_width, box_height in target_boxes
        ],
        "focus": (
            "target and nearby lines remain sharp; "
            "outer article crop has a soft focus falloff"
        ),
        "transition": (
            "direct page cuts with each sound aligned "
            "to the first audio sample of its cut frame"
        ),
        "camera": (
            "centered documentary push-in of up to 13% with "
            "subtle handheld shake; highlighted phrase stays centered"
        ),
        "font_styles": list(
            dict.fromkeys(
                (article.source or {}).get(
                    "font_style"
                )
                for article in articles
                if (article.source or {}).get(
                    "font_style"
                )
            )
        ),
    }
