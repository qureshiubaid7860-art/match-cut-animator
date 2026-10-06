from __future__ import annotations

import shutil
import subprocess
import tempfile
from math import gcd
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .. import config
from ..audio.soundscape import SFX_ASSETS, build_audio, canonical_sound_style
from ..models import Article

PLAYBACK_SPEED = 2.0


def ffmpeg_path() -> str:
    if config.FFMPEG_BINARY:
        if Path(config.FFMPEG_BINARY).is_file():
            return config.FFMPEG_BINARY
        raise RuntimeError(f"FFMPEG_BINARY points to a missing executable: {config.FFMPEG_BINARY}")
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("FFmpeg is unavailable. Install the project requirements or set FFMPEG_BINARY to an FFmpeg executable.") from exc


def _prepare_source(article: Article, width: int, height: int):
    with Image.open(article.path) as image:
        source = ImageOps.exif_transpose(image).convert("RGB")
    if not article.bbox:
        raise ValueError(f"{article.filename}: no target word box is available.")
    bbox = [float(value) for value in article.bbox]
    bbox[0] = max(0.0, min(bbox[0], source.width - 1.0))
    bbox[1] = max(0.0, min(bbox[1], source.height - 1.0))
    bbox[2] = max(2.0, min(bbox[2], source.width - bbox[0]))
    bbox[3] = max(2.0, min(bbox[3], source.height - bbox[1]))
    background = ImageOps.fit(
        source,
        (width, height),
        method=Image.Resampling.BILINEAR,
        centering=((bbox[0] + bbox[2] / 2) / source.width, (bbox[1] + bbox[3] / 2) / source.height),
    )
    background = ImageEnhance.Brightness(background.filter(ImageFilter.GaussianBlur(radius=42))).enhance(0.96)
    return source, bbox, background


def _draw_marker(
    frame: Image.Image,
    box: tuple[float, float, float, float],
    reveal_fraction: float = 1.0,
) -> Image.Image:
    """Lay an organic yellow pigment stroke behind dark printed letterforms."""
    reveal_fraction = max(0.0, min(1.0, reveal_fraction))
    if reveal_fraction <= 0.0:
        return frame
    x1, y1, x2, y2 = box
    word_width, word_height = max(2.0, x2 - x1), max(2.0, y2 - y1)
    pad_x = max(8, round(word_width * 0.055))
    stroke_width = max(12, round(word_width + pad_x * 2))
    stroke_height = max(8, round(word_height * 0.76))
    left = round(x1 - pad_x)
    top = round(y1 + word_height * 0.12)
    right = min(frame.width, left + stroke_width)
    bottom = min(frame.height, top + stroke_height)
    left = max(0, left)
    top = max(0, top)
    patch_width, patch_height = right - left, bottom - top
    if patch_width < 2 or patch_height < 2:
        return frame

    rng = np.random.default_rng(8197)
    mask = Image.new("L", (patch_width, patch_height), 0)
    brush = ImageDraw.Draw(mask)
    knots = max(8, min(18, round(patch_width / 28)))
    upper = []
    lower = []
    for index in range(knots + 1):
        x = index * (patch_width - 1) / knots
        jitter_top = float(rng.uniform(-0.10, 0.09))
        jitter_bottom = float(rng.uniform(-0.10, 0.10))
        upper.append((x, max(0, min(patch_height - 1, patch_height * (0.18 + jitter_top)))))
        lower.append((x, max(0, min(patch_height - 1, patch_height * (0.82 + jitter_bottom)))))
    brush.polygon(upper + list(reversed(lower)), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=0.65))
    mask_values = np.asarray(mask, dtype=np.float32)
    reveal_width = round(patch_width * reveal_fraction)
    mask_values[:, reveal_width:] = 0
    texture = rng.uniform(0.78, 1.0, size=mask_values.shape).astype(np.float32)
    original = frame.crop((left, top, right, bottom))
    pixels = np.asarray(original, dtype=np.float32)
    luminance = pixels[..., 0] * 0.2126 + pixels[..., 1] * 0.7152 + pixels[..., 2] * 0.0722
    ink_protection = np.clip((luminance - 18.0) / 74.0, 0.0, 1.0)
    alpha = np.clip(mask_values * texture * 0.82 * ink_protection, 0, 255).astype(np.uint8)
    marker_alpha = Image.fromarray(alpha)
    color = Image.new("RGB", (patch_width, patch_height), (249, 224, 40))
    frame.paste(Image.composite(color, original, marker_alpha), (left, top))
    return frame


def _draw_underline(
    frame: Image.Image,
    box: tuple[float, float, float, float],
    reveal_fraction: float,
) -> Image.Image:
    reveal_fraction = max(0.0, min(1.0, reveal_fraction))
    if reveal_fraction <= 0.0:
        return frame
    x1, y1, x2, y2 = box
    line_start = round(x1 - (x2 - x1) * 0.025)
    line_end = round(x1 + (x2 - x1) * reveal_fraction)
    line_y = round(y2 + max(3, (y2 - y1) * 0.1))
    line_width = max(4, round((y2 - y1) * 0.09))
    draw = ImageDraw.Draw(frame, "RGBA")
    draw.line(
        (line_start, line_y, line_end, line_y),
        fill=(222, 190, 20, 225),
        width=line_width,
    )
    return frame


def _focus_falloff(frame: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    """Keep the phrase and nearby article lines sharp; soften the outer crop."""
    x1, y1, x2, y2 = box
    margin_x = max(96, round((x2 - x1) * 0.22))
    margin_y = max(130, round((y2 - y1) * 1.45))
    core = (
        max(0, round(x1 - margin_x)), max(0, round(y1 - margin_y)),
        min(frame.width, round(x2 + margin_x)), min(frame.height, round(y2 + margin_y)),
    )
    mask = Image.new("L", frame.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(core, radius=max(36, margin_y // 2), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(48, round(frame.height * 0.035))))
    softened = frame.filter(ImageFilter.GaussianBlur(radius=4.2))
    return Image.composite(frame, softened, mask)


def _make_frame(
    source: Image.Image,
    bbox: list[float],
    background: Image.Image,
    width: int,
    height: int,
    target_box: tuple[float, float],
    highlight_mode: str = "highlight",
    reveal_fraction: float = 1.0,
) -> Image.Image:
    word_cx = bbox[0] + bbox[2] / 2
    word_cy = bbox[1] + bbox[3] / 2
    target_width, target_height = target_box
    scale_x = target_width / max(bbox[2], 1.0)
    scale_y = target_height / max(bbox[3], 1.0)
    left = word_cx - width / scale_x / 2
    top = word_cy - height / scale_y / 2
    affine = (1 / scale_x, 0, left, 0, 1 / scale_y, top)
    visible = source.transform(
        (width, height), Image.Transform.AFFINE, affine,
        resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0),
    )
    source_mask = Image.new("L", source.size, 255).transform(
        (width, height), Image.Transform.AFFINE, affine,
        resample=Image.Resampling.BICUBIC, fillcolor=0,
    )
    frame = background.copy()
    frame.paste(visible, (0, 0), source_mask)
    screen_box = (
        (width - target_width) / 2,
        (height - target_height) / 2,
        (width + target_width) / 2,
        (height + target_height) / 2,
    )
    paper_tone = Image.new("RGB", frame.size, (247, 243, 231))
    frame = Image.blend(frame, paper_tone, 0.045)
    frame = _focus_falloff(frame, screen_box)
    if highlight_mode in {"default", "highlight"}:
        return _draw_marker(frame, screen_box, 1.0 if highlight_mode == "default" else reveal_fraction)
    if highlight_mode == "underline":
        return _draw_underline(frame, screen_box, reveal_fraction)
    raise ValueError(f"Unsupported highlight mode: {highlight_mode}")


def _zoom_frame(frame: Image.Image, scale: float) -> Image.Image:
    if scale <= 1.0:
        return frame
    width, height = frame.size
    inverse_scale = 1.0 / scale
    offset_x = (width - width * inverse_scale) / 2
    offset_y = (height - height * inverse_scale) / 2
    return frame.transform(
        frame.size,
        Image.Transform.AFFINE,
        (inverse_scale, 0, offset_x, 0, inverse_scale, offset_y),
        resample=Image.Resampling.BICUBIC,
    )


def _timeline_progress(frame_index: int, total_frames: int) -> float:
    return frame_index / max(1, total_frames - 1)


def _target_box_for_page(
    bbox: list[float], target_word: str, width: int, height: int, page_index: int,
) -> tuple[float, float]:
    scale_beats = (0.92, 1.08, 0.88, 1.14, 0.96, 1.04)
    base_width = width * min(0.58, max(0.34, 0.30 + len(target_word.strip()) * 0.018))
    target_width = min(width * 0.67, max(width * 0.28, base_width * scale_beats[page_index % len(scale_beats)]))
    target_height = target_width * max(bbox[3], 1.0) / max(bbox[2], 1.0)
    if target_height > height * 0.19:
        target_width *= height * 0.19 / target_height
        target_height = height * 0.19
    return target_width, target_height


def _draw_caption(frame: Image.Image, caption: str) -> Image.Image:
    if not caption.strip():
        return frame
    font_size = max(18, round(min(frame.width, frame.height) * 0.026))
    font_path = None
    for path in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf"):
        try:
            ImageFont.truetype(path, font_size)
            font_path = path
            break
        except OSError:
            continue
    text = " ".join(caption.strip().split())[:80].upper()
    draw = ImageDraw.Draw(frame, "RGBA")
    margin_x = round(frame.width * 0.067)
    margin_y = round(frame.height * 0.04)
    max_text_width = frame.width - margin_x * 2 - round(font_size * 1.6)
    font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    while font_path and draw.textbbox((0, 0), text, font=font)[2] > max_text_width and font_size > 12:
        font_size -= 2
        font = ImageFont.truetype(font_path, font_size)
    padding_x = max(10, round(font_size * 0.8))
    padding_y = max(8, round(font_size * 0.55))
    left, bottom = margin_x, frame.height - margin_y
    text_top = bottom - font_size - padding_y
    bounds = draw.textbbox((left, text_top), text, font=font)
    right = min(frame.width - margin_x, bounds[2] + padding_x)
    draw.rounded_rectangle((left - round(padding_x * 0.65), text_top - padding_y, right, bottom), radius=max(5, round(font_size * 0.2)), fill=(13, 14, 12, 150))
    draw.text((left, text_top), text, font=font, fill=(241, 237, 224, 245))
    return frame


def _sound_style(requested: str, articles: list[Article]) -> str:
    style = (requested or "automatic").strip().lower()
    if style != "automatic":
        return canonical_sound_style(style)
    generated = sum(1 for article in articles if (article.source or {}).get("generated"))
    if generated >= max(1, len(articles) // 2):
        return "paper"
    if len(articles) <= 3:
        return "minimal"
    return canonical_sound_style("documentary")


def render_video(
    articles: list[Article], output_path: str | Path, duration: float = 10.0, fps: int = 30,
    width: int = 1080, height: int = 1920, sfx_enabled: bool = True, background_enabled: bool = True,
    sfx_volume: int = 72, background_volume: int = 32, caption: str = "", sound_style: str = "automatic",
    target_word: str = "", progress: Callable[[int, str], None] | None = None,
    sfx_id: str = "click", aspect_ratio: str | None = None, highlight_mode: str = "highlight",
) -> dict:
    if len(articles) < 3:
        raise ValueError("At least 3 OCR-confirmed article pages are required to render a match cut.")
    if width % 2 or height % 2:
        raise ValueError("The output width and height must be even numbers for H.264 encoding.")
    if sfx_id != "none" and sfx_id not in SFX_ASSETS:
        raise ValueError(f"Unsupported sound-effect ID: {sfx_id}")
    divisor = gcd(width, height)
    actual_aspect_ratio = f"{width // divisor}:{height // divisor}"
    if aspect_ratio and aspect_ratio != actual_aspect_ratio:
        raise ValueError(f"Aspect ratio {aspect_ratio} does not match the requested {width}x{height} output dimensions.")
    if not 3.0 <= duration <= 30.0:
        raise ValueError("Duration must be between 3 and 30 seconds.")
    if highlight_mode not in {"default", "highlight", "underline"}:
        raise ValueError(f"Unsupported highlight mode: {highlight_mode}")
    fps = max(24, min(int(fps), 60))
    output_duration = duration / PLAYBACK_SPEED
    total_frames = round(output_duration * fps)
    sources = [_prepare_source(article, width, height) for article in articles]
    boundaries = [round(index * total_frames / len(articles)) for index in range(len(articles) + 1)]
    cut_frames = boundaries[1:-1]
    selected_style = _sound_style(sound_style, articles)
    target_boxes = [
        _target_box_for_page(bbox, target_word, width, height, index)
        for index, (_, bbox, _) in enumerate(sources)
    ]
    page_frames = []
    for (source, bbox, background), target_box in zip(sources, target_boxes):
        frame = _make_frame(source, bbox, background, width, height, target_box, highlight_mode, 0.0)
        page_frames.append(_draw_caption(frame, caption))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="matchcut-") as temp_dir:
        audio_path = Path(temp_dir) / "sound-design.wav"
        if progress:
            progress(0, "Building the frame-locked sound timeline")
        build_audio(
            audio_path, output_duration, fps, cut_frames, sfx_enabled, background_enabled,
            sfx_volume, background_volume, selected_style, sfx_id=sfx_id,
        )
        command = [
            ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
            "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0",
            "-frames:v", str(total_frames), "-t", f"{output_duration:.4f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path),
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            assert process.stdin is not None
            article_index = 0
            for frame_index in range(total_frames):
                while article_index + 1 < len(articles) and frame_index >= boundaries[article_index + 1]:
                    article_index += 1
                page_start, page_end = boundaries[article_index], boundaries[article_index + 1]
                page_progress = (frame_index - page_start) / max(1, page_end - page_start - 1)
                timeline_progress = _timeline_progress(frame_index, total_frames)
                frame = page_frames[article_index].copy()
                target_box = target_boxes[article_index]
                screen_box = (
                    (width - target_box[0]) / 2,
                    (height - target_box[1]) / 2,
                    (width + target_box[0]) / 2,
                    (height + target_box[1]) / 2,
                )
                if highlight_mode == "default":
                    _draw_marker(frame, screen_box, 1.0)
                elif highlight_mode == "highlight":
                    _draw_marker(frame, screen_box, timeline_progress)
                else:
                    _draw_underline(frame, screen_box, timeline_progress)
                frame = _zoom_frame(frame, 1.0 + 0.045 * page_progress)
                process.stdin.write(np.asarray(frame, dtype=np.uint8).tobytes())
                if progress and (frame_index % max(1, fps // 2) == 0 or frame_index + 1 == total_frames):
                    progress(round((frame_index + 1) / total_frames * 100), f"Rendered frame {frame_index + 1} of {total_frames}")
            process.stdin.close()
            stderr = process.stderr.read() if process.stderr else b""
            if process.stderr:
                process.stderr.close()
            return_code = process.wait()
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stderr:
                process.stderr.close()
            output_path.unlink(missing_ok=True)
            raise
        if return_code != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            output_path.unlink(missing_ok=True)
            raise RuntimeError(f"FFmpeg could not render the video. {message[:700]}")
    if not output_path.is_file() or output_path.stat().st_size < 1_024:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("FFmpeg completed but the MP4 is missing or empty.")
    return {
        "width": width,
        "height": height,
        "aspect_ratio": actual_aspect_ratio,
        "fps": fps,
        "duration": round(total_frames / fps, 3),
        "requested_duration": duration,
        "playback_speed": PLAYBACK_SPEED,
        "frame_count": total_frames,
        "article_count": len(articles),
        "cut_frames": cut_frames,
        "cut_times": [round(frame / fps, 4) for frame in cut_frames],
        "audio_sample_rate": 48000,
        "audio_cut_frames": cut_frames,
        "codec": "H.264 / AAC",
        "sound_style": selected_style,
        "sfx_id": sfx_id,
        "sfx_asset": SFX_ASSETS.get(sfx_id) if sfx_id != "none" else None,
        "background_enabled": background_enabled,
        "background_volume": background_volume,
        "sfx_volume": sfx_volume,
        "highlight_mode": highlight_mode,
        "highlight": (
            "organic yellow pigment stroke shown at full strength for the default highlight"
            if highlight_mode == "default"
            else "organic yellow pigment stroke progressively revealed across the target" if highlight_mode == "highlight"
            else "yellow underline progressively drawn beneath the target"
        ),
        "target_lock": "centered phrase with a restrained scale rhythm across source images",
        "target_screen_bbox": [
            round((width - target_boxes[0][0]) / 2, 2),
            round((height - target_boxes[0][1]) / 2, 2),
            round(target_boxes[0][0], 2),
            round(target_boxes[0][1], 2),
        ],
        "target_box_sequence": [
            {
                "x": round((width - box_width) / 2, 2),
                "y": round((height - box_height) / 2, 2),
                "width": round(box_width, 2),
                "height": round(box_height, 2),
            }
            for box_width, box_height in target_boxes
        ],
        "focus": "target and nearby lines remain sharp; outer article crop has a soft focus falloff",
        "transition": "direct page cuts with each sound aligned to the first audio sample of its cut frame",
        "camera": "centered documentary push-in of up to 4.5% on each page; highlighted phrase stays centered",
        "font_styles": list(dict.fromkeys(
            (article.source or {}).get("font_style")
            for article in articles if (article.source or {}).get("font_style")
        )),
    }
