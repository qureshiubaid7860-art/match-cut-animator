import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.models import Article
from backend.app.services.acquisition import acquire_sources
from backend.app.video.renderer import render_video, _compose_page_frame, _target_box_for_page
from PIL import Image

session, articles = acquire_sources('TECHNOLOGY', 5)
print('Articles count:', len(articles))
for i, a in enumerate(articles):
    src = a.source or {}
    print(f"Article {i}: file={a.filename}, bbox={a.bbox}, generated={src.get('generated')}, title={src.get('headline')}")

out_dir = Path('render-test/debug_frames')
out_dir.mkdir(parents=True, exist_ok=True)

width, height = 1080, 1920
for i, a in enumerate(articles):
    target_box = _target_box_for_page([float(v) for v in a.bbox], 'TECHNOLOGY', width, height, i)
    frame = _compose_page_frame(a, width, height, target_box, 'default', 'TEST CAPTION')
    frame.save(out_dir / f"page_{i}.png")
    print(f"Saved page_{i}.png, target_box={target_box}")

out_video = Path('render-test/debug_run.mp4')
res = render_video(articles, out_video, duration=5.0, fps=30, width=width, height=height, target_word='TECHNOLOGY')
print("Rendered video:", res)
