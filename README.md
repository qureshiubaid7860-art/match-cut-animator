# MATCH CUT — Documentary Word Highlight Generator

MATCH CUT turns one target word into a short documentary-style match-cut video. Enter a word and generate: the backend searches public article indexes, retrieves accessible article text, renders an attributed editorial reconstruction, runs local OCR, ranks and orders exact-word matches, then creates an MP4. Public article pages are used when available; clearly labeled fictional editorial pages fill any remaining selected slots.

Public articles are shown as locally typeset excerpt reconstructions, not screenshots of the publisher's original page. The original source link and publisher appear in the completed source notes and on each reconstruction. Fictional fallback pages use distinct copy and carry a visible `FICTIONAL EDITORIAL` label; they are never presented as real reporting.

## Requirements

- Windows 10/11, macOS, or Linux
- Python 3.10–3.13
- Node.js 20 or later and npm
- Internet access for public article search

FFmpeg is supplied by `imageio-ffmpeg`. OCR uses RapidOCR with ONNX Runtime and local English recognition models.

## Install

From the project root, create and activate a Python environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install the frontend packages:

```powershell
cd frontend
npm install
cd ..
```

GDELT DOC public article search works without an API key. Optionally copy `.env.example` to `.env` and set `NEWSAPI_KEY` to add NewsAPI search results. Keys remain on the backend.

## Run

Open two terminals at the project root.

Terminal 1 — backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2 — frontend:

```powershell
cd frontend
npm run dev -- --host 127.0.0.1
```

Open the URL printed by Vite (usually http://127.0.0.1:5173). The frontend proxies `/api` and `/media` to the backend.

For a production frontend bundle, run `npm run build` in `frontend`; then the backend serves `frontend/dist` at http://127.0.0.1:8000.

## Use

1. Enter one word or a phrase of up to four words.
2. Choose the final video length, aspect ratio, and highlight mode. Optional page count (6, 12, 24, 36, 48, 60, or 72) and sound settings are inside **Sound & page options**. More pages create faster cuts within the chosen video length. **Moving Highlight** reveals the yellow stroke as each page plays; **Moving Underline** draws a line beneath the target. Use **Play** to preview a page-change sound.
3. Select **Generate Match Cut**. The finished MP4 keeps the length you selected and uses every selected page once.
4. Follow actual backend progress, preview the rendered MP4, and open the linked source notes.

The source worker searches GDELT DOC and direct publisher URLs from Bing News RSS. It fetches public article pages, extracts short source text containing the exact word, typesets an attributed reconstruction, and verifies its target box with OCR. Candidate quality uses OCR confidence, target size, and source resolution. The sequence minimizes changes in target-word geometry; rendering adds a restrained, repeatable size rhythm for a more visible match-cut cadence. If public sources fall short, it creates varied fictional editorial pages to reach the selected count. Duplicate article URLs, page files, and IDs are removed from the sequence so a page is never reused.

Each reconstructed newspaper page uses a rotating serif typeface. Match-cut ordering prefers a different typeface on successive pages. Rendering centers every OCR match and varies its scale slightly from page to page while preserving the printed word's proportions. Article pages change with direct cuts, followed by a centered documentary push-in of up to 4.5% on each page. Textured warm paper, a translucent hand-drawn highlighter or moving underline, and subtle focus falloff keep the target phrase prominent. Aspect ratios are 16:9 (landscape), 9:16 (vertical), 1:1 (square), 4:5 (portrait), and 4:3 (standard); the source framing and centered target are rebuilt for the chosen output dimensions rather than cropping a finished video. Page cuts and sound events share one frame timeline; each effect begins at the first audio sample of its corresponding cut frame, and clips are shortened if needed to avoid overlapping consecutive cuts. Export defaults are 1080 × 1920, 30 FPS, H.264 video, AAC audio, and MP4.

The default transition is a sharp mechanical click. Additional individually mapped WAV effects are Page Turn, Paper Flip, Paper Slide, Mouse Click (a supplied user recording), Soft Whoosh, Documentary Tick, Old Typewriter keys, Typewriter carriage return, Typewriter key and bell, Film Projector, Camera Shutter, Impact, Marker / Write, and **None**. Typewriter key, bell, and carriage sounds use real CC0 recordings, edited into per-cut clips; see [SFX-ATTRIBUTION.md](./frontend/public/audio/sfx/SFX-ATTRIBUTION.md). The old-typewriter option plays four recorded key strikes for each page change. Each transition plays one selected effect, synchronized to its cut frame, for at least four video frames; consecutive effects are truncated at the next cut rather than overlapped. The optional background ambience bed remains off by default so it cannot mask the selected effect. **None** produces a fully silent audio track, including no background bed. Aspect ratio and effect selection are remembered in the browser and are included explicitly in each generation request.

The maximum page count is 72 by default and can be reduced with `MAX_ARTICLES`. Each selected page appears once; locally generated editorial mock-ups fill any missing public article matches. The homepage and five static editorial landing pages are served with unique metadata and crawlable content. `/robots.txt` and `/sitemap.xml` are generated by the backend using the current request host.

## API

- `GET /api/health` and `GET /api/capabilities`
- `POST /api/generate` — accepts `target_word`, `number_of_articles`, `sound_style`, `sfx_id`, and `aspect_ratio`; source acquisition is automatic
- `POST /api/search` — acquire and OCR article pages without rendering
- `POST /api/upload` and `POST /api/analyze` — retained for API compatibility; the main UI does not require uploads
- `POST /api/demo` — run the local six-layout OCR/render pipeline
- `GET /api/progress/{job_id}`, `GET /api/result/{job_id}`, `GET /api/sources/{job_id}`
- `/media/...` — thumbnails and rendered MP4 files

Source images, thumbnails, and final videos are stored under `data/`. Set `MATCHCUT_DATA_DIR` to choose another local storage directory.

## Configuration

Copy `.env.example` to `.env` to configure:

- `NEWSAPI_KEY` (optional additional search provider)
- `MATCHCUT_DATA_DIR`, `MAX_UPLOAD_MB`, `MAX_UPLOAD_FILES`, `MAX_ARTICLES`, `MAX_PDF_PAGES`
- `DEFAULT_DURATION`, `DEFAULT_FPS`, `OUTPUT_WIDTH`, `OUTPUT_HEIGHT`
- `FFMPEG_BINARY` to override the bundled FFmpeg path

No API key is embedded in frontend code or checked into the project.
