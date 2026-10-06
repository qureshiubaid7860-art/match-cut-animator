from __future__ import annotations

import hashlib
import tempfile
import unittest
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image

from backend.app.api.routes import ASPECT_DIMENSIONS, GenerateRequest, SearchRequest
from backend.app.audio.soundscape import SFX_ASSETS, _SFX_DIRECTORY, _read_sfx, build_audio
from backend.app.models import Article
from backend.app.services.acquisition import _PAGE_FONT_FAMILIES, _fictional_page, order_for_match_cuts
from backend.app.services.demo import _generate_page
from backend.app.video.renderer import PLAYBACK_SPEED, _draw_marker, _draw_underline, _make_frame, _target_box_for_page, _timeline_progress, _zoom_frame, render_video


class AspectRatioTests(unittest.TestCase):
    def test_supported_ratios_resolve_to_exact_output_dimensions(self):
        expected = {
            "16:9": (1920, 1080),
            "9:16": (1080, 1920),
            "1:1": (1080, 1080),
            "4:5": (1080, 1350),
            "4:3": (1440, 1080),
        }
        self.assertEqual(ASPECT_DIMENSIONS, expected)
        for ratio, dimensions in expected.items():
            with self.subTest(ratio=ratio):
                request = GenerateRequest(target_word="NASA", aspect_ratio=ratio)
                self.assertEqual(ASPECT_DIMENSIONS[request.aspect_ratio], dimensions)
                self.assertEqual(dimensions[0] * int(ratio.split(":")[1]), dimensions[1] * int(ratio.split(":")[0]))

    def test_composition_is_rebuilt_for_each_canvas_shape(self):
        source = Image.new("RGB", (800, 600), (245, 242, 230))
        for ratio, (width, height) in ASPECT_DIMENSIONS.items():
            with self.subTest(ratio=ratio):
                frame = _make_frame(
                    source, [330, 260, 110, 35], Image.new("RGB", (width, height), "white"),
                    width, height, (width * 0.48, height * 0.1),
                )
                self.assertEqual(frame.size, (width, height))
                center_x = (width - width * 0.48) / 2
                center_y = (height - height * 0.1) / 2
                self.assertGreaterEqual(center_x, 0)
                self.assertGreaterEqual(center_y, 0)
                center = frame.getpixel((width // 2, height // 2))
                self.assertGreater(center[0], center[1])
                self.assertGreater(center[1], center[2])
                zoomed = _zoom_frame(frame, 1.025)
                zoomed_center = zoomed.getpixel((width // 2, height // 2))
                self.assertLessEqual(max(abs(left - right) for left, right in zip(zoomed_center, center)), 2)
                self.assertGreater(np.abs(np.asarray(frame, dtype=np.int16) - np.asarray(zoomed, dtype=np.int16)).sum(), 0)

    def test_match_phrase_stays_centered_with_reference_style_scale_rhythm(self):
        bbox = [240, 310, 160, 40]
        boxes = [_target_box_for_page(bbox, "TACO again", 1080, 1080, index) for index in range(6)]
        widths = [box[0] for box in boxes]

        self.assertGreater(max(widths) / min(widths), 1.2)
        for target_width, target_height in boxes:
            self.assertAlmostEqual(target_width / target_height, bbox[2] / bbox[3])
            self.assertLessEqual(target_width, 1080 * 0.67)
            self.assertLessEqual(target_height, 1080 * 0.19)


class HighlightModeTests(unittest.TestCase):
    def test_generate_request_accepts_only_supported_highlight_modes(self):
        self.assertEqual(GenerateRequest(target_word="NASA").highlight_mode, "default")
        self.assertEqual(GenerateRequest(target_word="NASA", highlight_mode="highlight").highlight_mode, "highlight")
        self.assertEqual(GenerateRequest(target_word="NASA", highlight_mode="underline").highlight_mode, "underline")
        with self.assertRaises(ValueError):
            GenerateRequest(target_word="NASA", highlight_mode="wipe")

    def test_highlighter_reveals_from_left_to_right(self):
        box = (40, 25, 160, 65)
        empty = Image.new("RGB", (200, 100), "white")
        half = _draw_marker(empty.copy(), box, 0.5)
        full = _draw_marker(empty.copy(), box, 1.0)
        half_pixels = np.asarray(half)
        full_pixels = np.asarray(full)

        self.assertEqual(np.asarray(empty).tolist(), np.asarray(_draw_marker(empty.copy(), box, 0.0)).tolist())
        self.assertGreater(np.count_nonzero(half_pixels[:, 40:100, 0] > half_pixels[:, 40:100, 1]), 0)
        self.assertEqual(np.count_nonzero(half_pixels[:, 105:160, 0] > half_pixels[:, 105:160, 1]), 0)
        self.assertGreater(np.count_nonzero(full_pixels[:, 105:160, 0] > full_pixels[:, 105:160, 1]), 0)

    def test_underline_reveals_toward_the_end_of_the_word(self):
        box = (40, 25, 160, 65)
        empty = Image.new("RGB", (200, 100), "white")
        half = _draw_underline(empty.copy(), box, 0.5)
        full = _draw_underline(empty.copy(), box, 1.0)
        half_pixels = np.asarray(half)
        full_pixels = np.asarray(full)

        self.assertEqual(np.asarray(empty).tolist(), np.asarray(_draw_underline(empty.copy(), box, 0.0)).tolist())
        self.assertGreater(np.count_nonzero(half_pixels[:, 40:100, 0] > half_pixels[:, 40:100, 1]), 0)
        self.assertEqual(np.count_nonzero(half_pixels[:, 105:160, 0] > half_pixels[:, 105:160, 1]), 0)
        self.assertGreater(np.count_nonzero(full_pixels[:, 105:160, 0] > full_pixels[:, 105:160, 1]), 0)

    def test_moving_effect_progress_spans_the_complete_video_timeline(self):
        self.assertEqual(_timeline_progress(0, 11), 0)
        self.assertEqual(_timeline_progress(5, 11), 0.5)
        self.assertEqual(_timeline_progress(10, 11), 1)


class PageSelectionTests(unittest.TestCase):
    def test_generation_and_search_accept_12_24_and_36_pages(self):
        for count in (12, 24, 36):
            with self.subTest(count=count):
                self.assertEqual(GenerateRequest(target_word="NASA", number_of_articles=count).number_of_articles, count)
                self.assertEqual(SearchRequest(target_word="NASA", number_of_articles=count).number_of_articles, count)
        with self.assertRaises(ValueError):
            GenerateRequest(target_word="NASA", number_of_articles=37)
        with self.assertRaises(ValueError):
            SearchRequest(target_word="NASA", number_of_articles=37)

    def test_repeated_article_ids_sources_and_page_files_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.jpg"
            image_path.write_bytes(b"same article page")
            original = Article(
                id="first", upload_id="session", filename="page.jpg", path=str(image_path),
                thumbnail_path="", source={"source_url": "https://example.com/story#section"},
                bbox=[10, 10, 40, 15], found=True, confidence=0.95, image_width=100, image_height=100,
            )
            duplicate_id = Article(
                id="first", upload_id="session", filename="duplicate-id.jpg", path=str(image_path),
                thumbnail_path="", bbox=[10, 10, 40, 15], found=True, confidence=0.95,
                image_width=100, image_height=100,
            )
            duplicate_source = Article(
                id="source-copy", upload_id="session", filename="copy.jpg", path=str(image_path),
                thumbnail_path="", source={"source_url": "https://example.com/story"},
                bbox=[10, 10, 40, 15], found=True, confidence=0.95, image_width=100, image_height=100,
            )
            duplicate_page = Article(
                id="page-copy", upload_id="session", filename="copy.jpg", path=str(image_path),
                thumbnail_path="", source={"source_url": "https://other.example/story"},
                bbox=[10, 10, 40, 15], found=True, confidence=0.95, image_width=100, image_height=100,
            )
            unique = order_for_match_cuts([original, duplicate_id, duplicate_source, duplicate_page])
        self.assertEqual([article.id for article in unique], ["first"])

    def test_fictional_fallback_editions_remain_distinct_after_36_pages(self):
        pages = [_fictional_page("TACO again", index) for index in (0, 8, 24, 35)]
        self.assertEqual(len({hashlib.sha256(page).digest() for _, page, _ in pages}), len(pages))
        self.assertEqual([source["edition"] for _, _, source in pages], [1, 9, 25, 36])

    def test_successive_newspaper_pages_change_font_family(self):
        pages = [_fictional_page("TACO again", index) for index in range(len(_PAGE_FONT_FAMILIES))]
        font_styles = [source["font_style"] for _, _, source in pages]
        self.assertEqual(font_styles, [family[0] for family in _PAGE_FONT_FAMILIES])
        self.assertEqual(len({hashlib.sha256(page).digest() for _, page, _ in pages}), len(pages))

    def test_demo_pages_render_with_rotating_font_families(self):
        with tempfile.TemporaryDirectory() as directory:
            dimensions, digests = [], []
            for index in range(len(_PAGE_FONT_FAMILIES)):
                path = Path(directory) / f"demo-{index}.jpg"
                _generate_page(index, path)
                with Image.open(path) as image:
                    dimensions.append(image.size)
                digests.append(hashlib.sha256(path.read_bytes()).digest())
        self.assertEqual(dimensions, [(1200, 1700)] * len(_PAGE_FONT_FAMILIES))
        self.assertEqual(len(set(digests)), len(_PAGE_FONT_FAMILIES))

    def test_match_cut_order_avoids_repeating_a_page_font_style(self):
        with tempfile.TemporaryDirectory() as directory:
            articles = []
            for index, font_style in enumerate(("Georgia", "Georgia", "Arial")):
                path = Path(directory) / f"styled-page-{index}.jpg"
                path.write_bytes(f"distinct-page-{index}".encode())
                articles.append(Article(
                    id=str(index), upload_id="session", filename=path.name,
                    path=str(path), thumbnail_path="", bbox=[10, 10, 40, 15],
                    source={"font_style": font_style}, found=True, confidence=0.95,
                    image_width=100, image_height=100,
                ))
            ordered = order_for_match_cuts(articles)
        styles = [(article.source or {})["font_style"] for article in ordered]
        self.assertTrue(all(left != right for left, right in zip(styles, styles[1:])))


class SoundEffectTests(unittest.TestCase):
    def test_sfx_selection_defaults_to_effect_only_not_the_ambience_bed(self):
        request = GenerateRequest(target_word="NASA", sfx_id="paper_flip")
        self.assertEqual(request.sfx_id, "paper_flip")
        self.assertFalse(request.background_enabled)
        self.assertEqual(GenerateRequest(target_word="NASA").sfx_id, "click")

    def test_supplied_mouse_click_is_selected_once_at_each_page_cut(self):
        request = GenerateRequest(target_word="NASA", sfx_id="mouse_click")
        self.assertEqual(request.sfx_id, "mouse_click")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "mouse-click.wav"
            build_audio(
                output, duration=1, fps=30, cut_frames=[10, 20],
                sfx_enabled=True, background_enabled=False,
                sfx_volume=100, background_volume=0, sound_style="documentary",
                sfx_id=request.sfx_id,
            )
            with wave.open(str(output), "rb") as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")

        expected_effect = (_read_sfx("mouse_click", 48000) * 32767).astype("<i2")
        first_cut = round(10 * 48000 / 30)
        second_cut = round(20 * 48000 / 30)
        self.assertTrue(np.all(samples[:first_cut] == 0))
        self.assertTrue(np.array_equal(samples[first_cut:first_cut + expected_effect.size], expected_effect))
        self.assertTrue(np.array_equal(samples[second_cut:second_cut + expected_effect.size], expected_effect))

    def test_two_times_render_shortens_mp4_and_keeps_each_page_once(self):
        with tempfile.TemporaryDirectory() as directory:
            articles = []
            for index, color in enumerate(("white", "#eee9dc", "#d8d3c7")):
                image_path = Path(directory) / f"page-{index}.jpg"
                Image.new("RGB", (240, 360), color).save(image_path)
                articles.append(Article(
                    id=str(index), upload_id="session", filename=image_path.name,
                    path=str(image_path), thumbnail_path="", bbox=[90, 160, 60, 24],
                    found=True, confidence=0.95, image_width=240, image_height=360,
                ))
            output = Path(directory) / "two-times.mp4"
            metadata = render_video(
                articles, output, duration=3, fps=24, width=160, height=240,
                sfx_enabled=False, background_enabled=False, target_word="NASA",
                sfx_id="none", aspect_ratio="2:3", highlight_mode="underline",
            )
            self.assertTrue(output.is_file())
            frame_stream = imageio_ffmpeg.read_frames(str(output))
            video_metadata = next(frame_stream)
            frame_stream.close()

        self.assertEqual(metadata["article_count"], 3)
        self.assertEqual(metadata["requested_duration"], 3)
        self.assertEqual(metadata["playback_speed"], PLAYBACK_SPEED)
        self.assertEqual(metadata["duration"], 1.5)
        self.assertEqual(metadata["frame_count"], 36)
        self.assertEqual(metadata["highlight_mode"], "underline")
        self.assertIn("underline progressively drawn", metadata["highlight"])
        self.assertAlmostEqual(video_metadata["duration"], 1.5, places=2)
        self.assertEqual(metadata["cut_frames"], metadata["audio_cut_frames"])

    def test_each_effect_has_a_unique_audio_asset_and_render(self):
        assets = [(_SFX_DIRECTORY / filename).read_bytes() for filename in SFX_ASSETS.values()]
        self.assertEqual(len({hashlib.sha256(asset).digest() for asset in assets}), len(SFX_ASSETS))

        rendered = []
        for sound_id in SFX_ASSETS:
            with self.subTest(sound_id=sound_id), tempfile.TemporaryDirectory() as directory:
                request = GenerateRequest(target_word="NASA", sfx_id=sound_id)
                self.assertEqual(request.sfx_id, sound_id)
                output = Path(directory) / "audio.wav"
                build_audio(
                    output, duration=1, fps=30, cut_frames=[10, 20],
                    sfx_enabled=True, background_enabled=False,
                    sfx_volume=100, background_volume=0, sound_style="documentary",
                    sfx_id=sound_id,
                )
                with wave.open(str(output), "rb") as audio:
                    samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
                effect_samples = _read_sfx(sound_id, 48000)
                first_cut = round(10 * 48000 / 30)
                second_cut = round(20 * 48000 / 30)
                self.assertGreaterEqual(effect_samples.size, round(48000 * 4 / 30))
                self.assertTrue(np.all(samples[:first_cut] == 0))
                self.assertNotEqual(samples[first_cut], 0)
                self.assertNotEqual(samples[second_cut], 0)
                rendered.append(hashlib.sha256(samples.tobytes()).digest())

        self.assertEqual(len(set(rendered)), len(SFX_ASSETS))

    def test_old_typewriter_has_four_distinct_key_strikes_per_cut(self):
        with wave.open(str(_SFX_DIRECTORY / SFX_ASSETS["typewriter"]), "rb") as audio:
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
            rate = audio.getframerate()
        self.assertGreaterEqual(samples.size, round(rate * 4 / 30))
        for strike_time in (0, .065, .131, .198):
            start = round(strike_time * rate)
            end = min(samples.size, start + round(rate * .012))
            with self.subTest(strike_time=strike_time):
                self.assertGreater(np.max(np.abs(samples[start:end])), 5000)

    def test_adjacent_cut_effects_start_on_their_frame_without_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "adjacent-cuts.wav"
            build_audio(
                output, duration=1, fps=30, cut_frames=[10, 11],
                sfx_enabled=True, background_enabled=False,
                sfx_volume=100, background_volume=0, sound_style="minimal",
                sfx_id="page_turn",
            )
            with wave.open(str(output), "rb") as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")

        frame_samples = round(48000 / 30)
        first_cut = 10 * frame_samples
        second_cut = 11 * frame_samples
        expected_first = (_read_sfx("page_turn", 48000)[:frame_samples] * 32767).astype("<i2")
        self.assertTrue(np.array_equal(samples[first_cut:second_cut], expected_first))
        self.assertEqual(samples[second_cut], int(_read_sfx("page_turn", 48000)[0] * 32767))

    def test_none_is_silent_even_when_background_is_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "silent.wav"
            build_audio(
                output, duration=1, fps=30, cut_frames=[10, 20],
                sfx_enabled=True, background_enabled=True,
                sfx_volume=100, background_volume=100, sound_style="cinematic",
                sfx_id="none",
            )
            with wave.open(str(output), "rb") as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        self.assertTrue(np.all(samples == 0))
        self.assertEqual(GenerateRequest(target_word="NASA", sfx_id="none").sfx_id, "none")


if __name__ == "__main__":
    unittest.main()
