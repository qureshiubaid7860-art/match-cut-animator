from __future__ import annotations

from pathlib import Path
import wave

import numpy as np


# Each preset controls the optional ambience bed; transition effects are separate WAV assets.
SOUND_PRESETS: dict[str, dict[str, float]] = {
    "minimal": {
        "tone_hz": 72, "tone_level": 0.12, "bed_texture": 0.0,
    },
    "documentary": {
        "tone_hz": 54, "tone_level": 0.32, "bed_texture": 0.20,
    },
    "paper": {
        "tone_hz": 0, "tone_level": 0.0, "bed_texture": 0.60,
    },
    "marker": {
        "tone_hz": 0, "tone_level": 0.0, "bed_texture": 0.15,
    },
    "impact": {
        "tone_hz": 0, "tone_level": 0.0, "bed_texture": 0.0,
    },
    "cinematic": {
        "tone_hz": 48, "tone_level": 0.50, "bed_texture": 0.14,
    },
}

_ALIASES = {"paper_shuffle": "paper", "soft_impact": "impact", "film_advance": "documentary"}
_SFX_DIRECTORY = Path(__file__).resolve().parents[3] / "frontend" / "public" / "audio" / "sfx"
SFX_ASSETS = {
    "page_turn": "page-turn.wav",
    "paper_flip": "paper-flip.wav",
    "paper_slide": "paper-slide.wav",
    "mouse_click": "mouse-click.wav",
    "soft_whoosh": "soft-whoosh.wav",
    "impact": "impact.wav",
    "click": "click.wav",
    "typewriter": "typewriter.wav",
    "typewriter_return": "typewriter-return.wav",
    "typewriter_bell": "typewriter-bell.wav",
    "editorial_tick": "editorial-tick.wav",
    "film_projector": "film-projector.wav",
    "camera_shutter": "camera-shutter.wav",
    "marker_write": "marker-write.wav",
}


def canonical_sound_style(value: str) -> str:
    style = (value or "documentary").strip().lower().replace(" ", "_")
    style = _ALIASES.get(style, style)
    return style if style in SOUND_PRESETS else "documentary"


def _read_sfx(sound_id: str, sample_rate: int) -> np.ndarray:
    filename = SFX_ASSETS.get(sound_id)
    if not filename:
        raise ValueError(f"Unsupported sound-effect ID: {sound_id}")
    asset_path = _SFX_DIRECTORY / filename
    try:
        with wave.open(str(asset_path), "rb") as asset:
            channels = asset.getnchannels()
            source_rate = asset.getframerate()
            samples = np.frombuffer(asset.readframes(asset.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    except (OSError, wave.Error) as exc:
        raise RuntimeError(f"Could not load sound-effect asset '{filename}': {exc}") from exc
    if not samples.size:
        raise RuntimeError(f"Sound-effect asset '{filename}' contains no samples.")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if source_rate != sample_rate:
        source_times = np.arange(samples.size, dtype=np.float64) / source_rate
        target_times = np.arange(round(samples.size * sample_rate / source_rate), dtype=np.float64) / sample_rate
        samples = np.interp(target_times, source_times, samples).astype(np.float32)
    return samples


def build_audio(
    path: str | Path,
    duration: float,
    fps: int,
    cut_frames: list[int],
    sfx_enabled: bool,
    background_enabled: bool,
    sfx_volume: int,
    background_volume: int,
    sound_style: str = "documentary",
    sample_rate: int = 48000,
    sfx_id: str = "click",
) -> None:
    """Mix the selected SFX asset onto the exact frame-locked cut timeline."""
    sample_count = round(duration * sample_rate)
    mix = np.zeros(sample_count, dtype=np.float32)
    style = canonical_sound_style(sound_style)
    preset = SOUND_PRESETS[style]
    if sfx_id != "none" and sfx_id not in SFX_ASSETS:
        raise ValueError(f"Unsupported sound-effect ID: {sfx_id}")

    if sfx_id != "none" and background_enabled and background_volume and sample_count:
        t = np.arange(sample_count, dtype=np.float32) / sample_rate
        rng = np.random.default_rng(4042)
        noise = rng.normal(0.0, 1.0, sample_count).astype(np.float32)
        texture = np.convolve(noise, np.ones(41, dtype=np.float32) / 41, mode="same")
        tone_hz = preset["tone_hz"]
        tonal_bed = (
            np.sin(2 * np.pi * tone_hz * t)
            + 0.36 * np.sin(2 * np.pi * (tone_hz * 1.5) * t)
            if tone_hz else np.zeros(sample_count, dtype=np.float32)
        )
        bed = tonal_bed * preset["tone_level"] + texture * preset["bed_texture"]
        bed *= background_volume / 100.0
        bed *= 0.035 if style == "minimal" else 0.055
        mix += bed.astype(np.float32)

    if sfx_id != "none" and sfx_enabled and sfx_volume:
        effect_asset = _read_sfx(sfx_id, sample_rate)
        for cut_number, frame in enumerate(cut_frames):
            # Snap the sound event to the first audio sample of this video frame.
            start = round(frame * sample_rate / fps)
            if start >= sample_count:
                continue
            next_start = (
                round(cut_frames[cut_number + 1] * sample_rate / fps)
                if cut_number + 1 < len(cut_frames) else sample_count
            )
            length = min(effect_asset.size, sample_count - start, next_start - start)
            if length <= 0:
                continue
            mix[start:start + length] += effect_asset[:length] * (sfx_volume / 100.0)

    peak = float(np.max(np.abs(mix))) if sample_count else 0.0
    if peak > 0.82:
        mix *= 0.82 / peak
    pcm = (np.clip(mix, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
