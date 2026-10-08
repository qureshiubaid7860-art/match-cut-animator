from pathlib import Path
from PIL import Image
from backend.app.models import Article
from backend.app.video.renderer import render_video
import time

root = Path("render-test")
root.mkdir(exist_ok=True)

articles = []

for i in range(3):
    p = root / f"page{i}.jpg"
    Image.new("RGB", (500, 700), "white").save(p)

    articles.append(
        Article(
            id=f"test{i}",
            upload_id="test",
            filename=p.name,
            path=str(p),
            thumbnail_path=str(p),
            source={"generated": True},
            found=True,
            confidence=1.0,
            bbox=[100, 250, 300, 80],
            image_width=500,
            image_height=700,
        )
    )

out = root / "test.mp4"

t = time.time()

result = render_video(
    articles,
    out,
    duration=3.0,
    fps=24,
    width=360,
    height=640,
    sfx_enabled=False,
    background_enabled=False,
    sfx_id="none",
    target_word="TEST",
    highlight_mode="highlight",
    progress=lambda v, m: print(f"[{v}%] {m}", flush=True),
)

print("DONE:", out)
print("SIZE:", out.stat().st_size)
print("TIME:", round(time.time() - t, 2), "sec")
print("FRAMES:", result["frame_count"])
