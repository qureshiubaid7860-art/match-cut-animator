import sys, os, glob, pathlib
sys.path.append('D:/ubaid/Match Cut Animater')
from backend.app.video.renderer import render_video
from backend.app.models import Article

# Find some sample images
image_paths = sorted(glob.glob('D:/ubaid/Match Cut Animater/data/input/*.jpg'))[:3]
if not image_paths:
    print('No images found')
    sys.exit(1)
articles = []
for idx, path in enumerate(image_paths):
    articles.append(Article(
        id=str(idx),
        path=path,
        filename=os.path.basename(path),
        image_width=1080,
        image_height=1920,
        bbox=[100,100,200,50],
        source_url='',
        source_kind='public',
        source_title='Dummy',
        source_author='',
        source_date='2020-01-01',
        source_body='',
        source_url_full=''
    ))

output_path = pathlib.Path('D:/ubaid/Match Cut Animater/data/output/test_video.mp4')
debug_dir = pathlib.Path('D:/ubaid/Match Cut Animater/data/debug')
render_video(
    articles=articles,
    target_word='Test',
    highlight_mode='highlight',
    caption='',
    fps=30,
    duration=2,
    audio_style='basic',
    sfx_id='page_turn',
    output_path=output_path,
    progress=None,
    debug_dir=debug_dir,
)
print('Render complete')
