# MATCH CUT frontend

The React and Vite interface accepts one target word, with optional article-count and sound-style choices. It sends an automatic generation request, shows backend-reported progress, previews the rendered MP4, and lists credited article sources and clearly marked fictional fallbacks.

## Run locally

Start FastAPI from the repository root on port 8000:

```sh
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Then run Vite:

```sh
npm install
npm run dev -- --host 127.0.0.1
```

Vite proxies `/api` and `/media` to `http://127.0.0.1:8000` when `VITE_API_ORIGIN` is empty. To use a deployed FastAPI backend during local frontend development, set `VITE_API_ORIGIN` in `.env.development` to the backend origin (without `/api`) and restart Vite. The checked-in local setup targets the current FastAPI Cloud deployment. Set the same variable in the frontend hosting provider before building for production. API calls, generated MP4s, and source thumbnails use this origin.

## API flow

- `GET /api/capabilities` reports public-search and fallback availability.
- `POST /api/generate` accepts `target_word`, `number_of_articles`, and `sound_style`; `upload_id` is optional for API compatibility.
- The UI polls `GET /api/progress/{job_id}`. Completion returns a real `video_url` and render metadata from `GET /api/result/{job_id}`.
- `GET /api/sources/{job_id}` returns source links, OCR confidence, and fictional/source labels.

Progress reflects backend work. The interface does not simulate search, OCR, rendering, or completion.
