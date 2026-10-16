import React, { useEffect, useRef, useState } from 'react';
import rewardedAdService, { isRewardedAdGateEnabled } from './services/rewardedAd.js';

const API = (import.meta.env.VITE_API_ORIGIN || '').replace(/\/+$/, '');
const ASPECT_RATIOS = [
  { id: '16:9', label: '16:9 — Landscape', shortLabel: 'LANDSCAPE', width: 1920, height: 1080 },
  { id: '9:16', label: '9:16 — Vertical / Shorts / Reels / TikTok', shortLabel: 'VERTICAL', width: 1080, height: 1920 },
  { id: '1:1', label: '1:1 — Square', shortLabel: 'SQUARE', width: 1080, height: 1080 },
  { id: '4:5', label: '4:5 — Instagram Portrait', shortLabel: 'PORTRAIT', width: 1080, height: 1350 },
  { id: '4:3', label: '4:3 — Standard', shortLabel: 'STANDARD', width: 1440, height: 1080 },
];
const HIGHLIGHT_MODES = [
  { id: 'default', label: 'Default Highlight' },
  { id: 'highlight', label: 'Moving Highlight' },
  { id: 'underline', label: 'Moving Underline' },
];
const PAGE_COUNTS = [6, 12, 24, 36, 48, 60, 72];
const SOUND_EFFECTS = [
  { id: 'page_turn', label: 'Page Turn', asset: '/audio/sfx/page-turn.wav' },
  { id: 'paper_flip', label: 'Paper Flip', asset: '/audio/sfx/paper-flip.wav' },
  { id: 'paper_slide', label: 'Paper Slide', asset: '/audio/sfx/paper-slide.wav' },
  { id: 'mouse_click', label: 'Mouse Click', asset: '/audio/sfx/mouse-click.wav' },
  { id: 'soft_whoosh', label: 'Soft Whoosh', asset: '/audio/sfx/soft-whoosh.wav' },
  { id: 'editorial_tick', label: 'Documentary Tick', asset: '/audio/sfx/editorial-tick.wav' },
  { id: 'click', label: 'Mechanical Click', asset: '/audio/sfx/click.wav' },
  { id: 'typewriter', label: 'Old Typewriter — Keys', asset: '/audio/sfx/typewriter.wav' },
  { id: 'typewriter_return', label: 'Typewriter — Carriage Return', asset: '/audio/sfx/typewriter-return.wav' },
  { id: 'typewriter_bell', label: 'Typewriter — Key + Bell', asset: '/audio/sfx/typewriter-bell.wav' },
  { id: 'film_projector', label: 'Film Projector', asset: '/audio/sfx/film-projector.wav' },
  { id: 'camera_shutter', label: 'Camera Shutter', asset: '/audio/sfx/camera-shutter.wav' },
  { id: 'impact', label: 'Impact', asset: '/audio/sfx/impact.wav' },
  { id: 'marker_write', label: 'Marker / Write', asset: '/audio/sfx/marker-write.wav' },
  { id: 'none', label: 'None', asset: '' },
];

function savedPreference(key, choices, fallback) {
  const stored = window.localStorage.getItem(key);
  return choices.some((choice) => choice.id === stored) ? stored : fallback;
}

function savedPageCount() {
  const directChoice = Number(window.localStorage.getItem('matchcut.articleCount'));
  if (PAGE_COUNTS.includes(directChoice)) return String(directChoice);
  return '36';
}

function savedRange(key, fallback, min, max) {
  const stored = window.localStorage.getItem(key);
  if (stored === null) return fallback;
  const value = Number(stored);
  return Number.isFinite(value) && value >= min && value <= max ? value : fallback;
}

function Icon({ name, size = 18 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true };
  const shapes = {
    mark: <><path d="M4 4h6v16H4zM14 4h6v16h-6z" /><path d="M7 8h10M7 16h10" /></>,
    arrow: <><path d="M4 12h15m-6-6 6 6-6 6" /></>,
    download: <><path d="M12 4v12m0 0 4-4m-4 4-4-4" /><path d="M5 20h14" /></>,
    external: <><path d="M14 4h6v6m0-6-9 9" /><path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6" /></>,
    spinner: <><path d="M20 12a8 8 0 1 1-2.4-5.7" /><path d="M20 4v5h-5" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    down: <path d="m6 9 6 6 6-6" />,
  };
  return <svg {...common}>{shapes[name] || null}</svg>;
}

async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API}${path}`, { credentials: 'same-origin', ...options });
  } catch {
    throw new Error('The MATCH CUT service is unreachable. Check your connection and try again.');
  }
  const type = response.headers.get('content-type') || '';
  const payload = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status === 429) {
      throw new Error(typeof payload === 'object' ? payload?.detail || 'Another video is already rendering. Wait for it to finish, then try again.' : payload);
    }
    if (response.status === 502 && !type.includes('application/json')) {
      throw new Error('The service could not finish this video. Please try again in a moment.');
    }
    const message = typeof payload === 'object' ? payload?.detail || payload?.message || payload?.error : payload;
    throw new Error(message || `Request failed (${response.status}).`);
  }
  return payload;
}

function mediaUrl(path) {
  if (!path || /^https?:\/\//i.test(path)) return path || '';
  return `${API}${path.startsWith('/') ? path : `/${path}`}`;
}

function sourceHost(url) {
  try { return new URL(url).hostname; } catch { return 'Public article'; }
}

function progressCopy(job) {
  if (job?.stage) return job.stage.replace(/^\d+\s+—\s+/, '');
  if (!job || job.status === 'queued') return 'Getting started…';
  const percent = Number(job.percent || 0);
  if (percent < 20) return 'Looking for pages…';
  if (percent < 48) return 'Checking the words on each page…';
  if (percent < 70) return 'Lining up your phrase…';
  if (percent < 99) return 'Making your MP4…';
  return 'Finishing your video…';
}

function DemoVideo({ title, video, poster, mode }) {
  const videoRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  function startPreview() {
    const preview = videoRef.current;
    if (!preview) return;
    preview.play().catch(() => setPlaying(false));
  }

  function pausePreview() {
    videoRef.current?.pause();
  }

  function togglePreview() {
    const preview = videoRef.current;
    if (!preview) return;
    if (preview.paused) startPreview();
    else pausePreview();
  }

  function onPreviewKeyDown(event) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      togglePreview();
    }
  }

  return (
    <div
      className={`demo-media${playing ? ' is-playing' : ''}`}
      role="button"
      tabIndex={unavailable ? -1 : 0}
      aria-label={`${playing ? 'Pause' : 'Play'} silent ${title} example using ${mode}; hover to preview, tap or press Enter to toggle`}
      aria-pressed={playing}
      onPointerEnter={(event) => { if (event.pointerType !== 'touch') startPreview(); }}
      onPointerLeave={(event) => { if (event.pointerType !== 'touch') pausePreview(); }}
      onClick={togglePreview}
      onKeyDown={onPreviewKeyDown}
    >
      {unavailable ? (
        <div className="demo-media-fallback" role="img" aria-label={`${title} example preview`} style={{ backgroundImage: `linear-gradient(180deg, rgba(12, 17, 22, .08), rgba(12, 17, 22, .78)), url("${poster}")` }}>
          <span className="demo-fallback-label">EXAMPLE PREVIEW</span>
          <p>Video preview is unavailable right now.</p>
        </div>
      ) : (
        <video
          ref={videoRef}
          src={video}
          muted
          loop
          playsInline
          preload="none"
          poster={poster}
          aria-hidden="true"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onError={() => setUnavailable(true)}
        >
          Your browser does not support video playback.
        </video>
      )}
      {!unavailable && <span className="demo-play-hint" aria-hidden="true">{playing ? 'TAP TO PAUSE' : 'HOVER OR TAP TO PREVIEW'}</span>}
    </div>
  );
}

function App() {
  const [theme, setTheme] = useState(() => window.localStorage.getItem('matchcut.theme') === 'light' ? 'light' : 'dark');
  const [targetWord, setTargetWord] = useState('');
  const [duration, setDuration] = useState('6');
  const [articleCount, setArticleCount] = useState(savedPageCount);
  const [zoomLevel, setZoomLevel] = useState(() => savedRange('matchcut.zoomLevel', 1.5, 1, 2));
  const [blurIntensity, setBlurIntensity] = useState(() => savedRange('matchcut.blurIntensity', 1, 0, 2.5));
  const [aspectRatio, setAspectRatio] = useState(() => savedPreference('matchcut.aspectRatio', ASPECT_RATIOS, '9:16'));
  const [highlightMode, setHighlightMode] = useState(() => savedPreference('matchcut.highlightMode', HIGHLIGHT_MODES, 'default'));
  const [soundStyle, setSoundStyle] = useState('documentary');
  const [sfxId, setSfxId] = useState(() => savedPreference('matchcut.sfxId', SOUND_EFFECTS, 'click'));
  const [backgroundEnabled, setBackgroundEnabled] = useState(false);
  const [previewingSfx, setPreviewingSfx] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const previewAudio = useRef(null);
  const videoRef = useRef(null);
  const compositionRef = useRef(null);
  const [serviceNote, setServiceNote] = useState('Checking source search…');
  const [job, setJob] = useState(null);
  const [videoUrl, setVideoUrl] = useState('');
  const [metadata, setMetadata] = useState(null);
  const [exportSettings, setExportSettings] = useState(null);
  const [sources, setSources] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [wordError, setWordError] = useState('');
  const [adDialog, setAdDialog] = useState(null);
  const [adMessage, setAdMessage] = useState('');
  const [adBusy, setAdBusy] = useState(false);
  const adSession = useRef(null);
  const adRequestId = useRef(0);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackForm, setFeedbackForm] = useState({ rating: '5', issueType: 'Feature request', message: '' });
  const [feedbackStatus, setFeedbackStatus] = useState('');

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem('matchcut.theme', theme);
  }, [theme]);

  useEffect(() => {
    window.localStorage.setItem('matchcut.aspectRatio', aspectRatio);
  }, [aspectRatio]);

  useEffect(() => {
    window.localStorage.setItem('matchcut.highlightMode', highlightMode);
  }, [highlightMode]);

  useEffect(() => {
    window.localStorage.setItem('matchcut.articleCount', articleCount);
  }, [articleCount]);

  useEffect(() => {
    window.localStorage.setItem('matchcut.zoomLevel', String(zoomLevel));
  }, [zoomLevel]);

  useEffect(() => {
    window.localStorage.setItem('matchcut.blurIntensity', String(blurIntensity));
  }, [blurIntensity]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !videoUrl || !metadata) return undefined;

    let animationFrame = 0;
    const updateComposition = () => {
      const composition = compositionRef.current;
      const durationSeconds = video.duration || Number(metadata.duration);
      if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) return;

      const currentTime = Math.max(0, Math.min(video.currentTime, durationSeconds));
      const progress = currentTime / durationSeconds;
      if (composition) {
        if (highlightMode === 'default') {
          composition.style.setProperty('--camera-x', '0%');
          composition.style.setProperty('--camera-y', '0%');
          composition.style.setProperty('--camera-zoom', '1');
        } else {
          const cameraPhase = progress * Math.PI * 2;
          composition.style.setProperty('--camera-x', `${1.15 * Math.sin(cameraPhase)}%`);
          composition.style.setProperty('--camera-y', `${0.72 * Math.sin(cameraPhase + Math.PI / 2)}%`);
          composition.style.setProperty('--camera-zoom', String(1.035 + 0.025 * (1 - Math.cos(cameraPhase))));
        }
      }
    };
    const stopAnimation = () => {
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      animationFrame = 0;
    };
    const syncPlayback = () => {
      updateComposition();
      if (!video.paused && !video.ended && !animationFrame) {
        const tick = () => {
          animationFrame = 0;
          updateComposition();
          if (!video.paused && !video.ended) animationFrame = window.requestAnimationFrame(tick);
        };
        animationFrame = window.requestAnimationFrame(tick);
      }
    };
    const pausePlayback = () => {
      stopAnimation();
      updateComposition();
    };

    video.addEventListener('loadedmetadata', syncPlayback);
    video.addEventListener('durationchange', syncPlayback);
    video.addEventListener('timeupdate', syncPlayback);
    video.addEventListener('seeking', syncPlayback);
    video.addEventListener('seeked', syncPlayback);
    video.addEventListener('play', syncPlayback);
    video.addEventListener('pause', pausePlayback);
    video.addEventListener('ended', pausePlayback);
    syncPlayback();

    return () => {
      stopAnimation();
      video.removeEventListener('loadedmetadata', syncPlayback);
      video.removeEventListener('durationchange', syncPlayback);
      video.removeEventListener('timeupdate', syncPlayback);
      video.removeEventListener('seeking', syncPlayback);
      video.removeEventListener('seeked', syncPlayback);
      video.removeEventListener('play', syncPlayback);
      video.removeEventListener('pause', pausePlayback);
      video.removeEventListener('ended', pausePlayback);
    };
  }, [metadata, videoUrl, highlightMode]);

  useEffect(() => {
    window.localStorage.setItem('matchcut.sfxId', sfxId);
    setPreviewError('');
    setPreviewingSfx(false);
    if (previewAudio.current) {
      previewAudio.current.pause();
      previewAudio.current.currentTime = 0;
    }
  }, [sfxId]);

  useEffect(() => {
    let active = true;
    apiRequest('/api/capabilities').then((result) => {
      if (active) setServiceNote(result.provider_message || (result.auto_search_configured ? 'Public article pages are searched, with generated pages filling any missing slots.' : 'Automatic page search is unavailable.'));
    }).catch(() => {
      if (active) setServiceNote('Public page search could not be confirmed yet. You can still try generating.');
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const savedJobId = window.localStorage.getItem('matchcut.activeJobId')
      || window.localStorage.getItem('matchcut.lastJobId');
    if (!savedJobId) return;
    const restore = async () => {
      setBusy(true);
      try {
        const savedSettings = JSON.parse(window.localStorage.getItem('matchcut.jobSettings') || '{}');
        if (savedSettings.target_word) setTargetWord(savedSettings.target_word);
        if (savedSettings.duration) setDuration(String(savedSettings.duration));
        const savedZoomLevel = Number(savedSettings.zoom_level);
        if (Number.isFinite(savedZoomLevel) && savedZoomLevel >= 1 && savedZoomLevel <= 2) setZoomLevel(savedZoomLevel);
        const savedBlurIntensity = Number(savedSettings.blur_intensity);
        if (Number.isFinite(savedBlurIntensity) && savedBlurIntensity >= 0 && savedBlurIntensity <= 2.5) setBlurIntensity(savedBlurIntensity);
        const preferredPageCount = Number(window.localStorage.getItem('matchcut.articleCount'));
        setArticleCount(String(PAGE_COUNTS.includes(preferredPageCount) ? preferredPageCount : 36));
        if (savedSettings.aspect_ratio) setAspectRatio(savedSettings.aspect_ratio);
        if (savedSettings.highlight_mode) setHighlightMode(savedSettings.highlight_mode);
        if (savedSettings.sfx_id) setSfxId(savedSettings.sfx_id);
        if (savedSettings.sound_style) setSoundStyle(savedSettings.sound_style);
        if (typeof savedSettings.background_enabled === 'boolean') setBackgroundEnabled(savedSettings.background_enabled);
        const progress = await apiRequest(`/api/progress/${encodeURIComponent(savedJobId)}`);
        setJob({ ...progress, id: progress.job_id || savedJobId, target_word: savedSettings.target_word || targetWord });
        if (progress.status === 'complete') {
          const result = await apiRequest(`/api/result/${encodeURIComponent(savedJobId)}`);
          if (result.video_url) {
            setVideoUrl(result.video_url);
            setMetadata(result.metadata || null);
            const sourceResult = await apiRequest(`/api/sources/${encodeURIComponent(savedJobId)}`);
            setSources(Array.isArray(sourceResult.sources) ? sourceResult.sources : []);
            setExportSettings(savedSettings);
          }
          window.localStorage.removeItem('matchcut.activeJobId');
          setBusy(false);
          return;
        }
        if (progress.status === 'failed') {
          setError(progress.error || 'The prior generation failed.');
          window.localStorage.removeItem('matchcut.activeJobId');
          window.localStorage.removeItem('matchcut.lastJobId');
          window.localStorage.removeItem('matchcut.jobSettings');
          setBusy(false);
          return;
        }
        await followJob(savedJobId);
      } catch (caught) {
        setError(caught.message || 'This generation could not be resumed.');
        window.localStorage.removeItem('matchcut.activeJobId');
        window.localStorage.removeItem('matchcut.lastJobId');
        window.localStorage.removeItem('matchcut.jobSettings');
      } finally {
        setBusy(false);
      }
    };
    restore();
  }, []);

  async function followJob(jobId) {
    if (!jobId) return;
    window.localStorage.setItem('matchcut.activeJobId', jobId);

    for (let attempt = 0; attempt < 900; attempt += 1) {
      const progress = await apiRequest(`/api/progress/${encodeURIComponent(jobId)}`);
      setJob({ ...progress, id: progress.job_id || jobId, target_word: targetWord });
      if (progress.status === 'failed') {
        window.localStorage.removeItem('matchcut.activeJobId');
        window.localStorage.removeItem('matchcut.lastJobId');
        window.localStorage.removeItem('matchcut.jobSettings');
        throw new Error(progress.error || progress.message || 'Video rendering failed.');
      }
      if (progress.status === 'complete') {
        const [result, sourceResult] = await Promise.all([
          apiRequest(`/api/result/${encodeURIComponent(jobId)}`),
          apiRequest(`/api/sources/${encodeURIComponent(jobId)}`),
        ]);
        if (!result.video_url) {
          window.localStorage.removeItem('matchcut.activeJobId');
          throw new Error(result.error || 'The render finished without an MP4 file.');
        }
        setVideoUrl(result.video_url);
        setMetadata(result.metadata || null);
        setSources(Array.isArray(sourceResult.sources) ? sourceResult.sources : []);
        window.localStorage.removeItem('matchcut.activeJobId');
        return;
      }
      if (progress.status === 'cancelled') {
        window.localStorage.removeItem('matchcut.activeJobId');
        window.localStorage.removeItem('matchcut.lastJobId');
        window.localStorage.removeItem('matchcut.jobSettings');
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 900));
    }
    throw new Error('This video is taking longer than expected. Try again in a moment.');
  }

  async function beginGeneration() {
    if (busy) return;
    setWordError('');
    setError('');
    setBusy(true);
    setVideoUrl('');
    setMetadata(null);
    const submittedSettings = {
      target_word: targetWord.trim(),
      number_of_articles: Number(articleCount),
      zoom_level: Number(zoomLevel),
      blur_intensity: Number(blurIntensity),
      sound_style: soundStyle,
      sfx_id: sfxId,
      background_enabled: backgroundEnabled,
      aspect_ratio: aspectRatio,
      duration: Number(duration),
      highlight_mode: highlightMode,
    };
    setExportSettings(submittedSettings);
    window.localStorage.setItem('matchcut.jobSettings', JSON.stringify(submittedSettings));
    setSources([]);
    setJob({ status: 'queued', percent: 1, message: 'Starting your video render…' });
    const selectedRatio = ASPECT_RATIOS.find((ratio) => ratio.id === aspectRatio);
    try {
      const started = await apiRequest('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...submittedSettings,
          fps: 30,
          width: selectedRatio.width,
          height: selectedRatio.height,
        }),
      });
      window.localStorage.setItem('matchcut.lastJobId', started.job_id);
      window.localStorage.setItem('matchcut.activeJobId', started.job_id);
      await followJob(started.job_id);
    } catch (caught) {
      const message = caught.message || 'Please try again.';
      setError(message);
      setJob((current) => ({ ...current, status: 'failed', error: message }));
      window.localStorage.removeItem('matchcut.activeJobId');
      window.localStorage.removeItem('matchcut.lastJobId');
      window.localStorage.removeItem('matchcut.jobSettings');
    } finally {
      setBusy(false);
    }
  }

  async function generate(event) {
    event?.preventDefault();
    if (busy || adBusy || (adDialog && adDialog !== 'unavailable')) return;
    const phrase = targetWord.trim();
    const words = phrase.match(/[^\W_]+/gu) || [];
    if (!phrase || words.length > 4) {
      setWordError('Enter a word or a short phrase of up to 4 words.');
      return;
    }

    setError('');
    if (!isRewardedAdGateEnabled()) {
      await beginGeneration();
      return;
    }

    setAdMessage('');
    setAdBusy(true);
    const requestId = ++adRequestId.current;
    try {
      const result = await rewardedAdService.prepareRewardedAd();
      if (requestId !== adRequestId.current) {
        result.cancel?.();
        return;
      }
      if (result.available) {
        adSession.current = result;
        setAdDialog('ready');
      } else {
        adSession.current = null;
        setAdMessage(result.reason || 'A rewarded ad is not available right now.');
        setAdDialog('unavailable');
      }
    } catch (adError) {
      if (requestId !== adRequestId.current) return;
      adSession.current = null;
      setAdMessage(adError.message || 'The rewarded ad could not be prepared.');
      setAdDialog('unavailable');
    } finally {
      if (requestId === adRequestId.current) setAdBusy(false);
    }
  }

  async function watchRewardedAd() {
    if (adBusy || adDialog !== 'ready') return;
    if (!adSession.current) {
      setAdMessage('The ad session expired. Try requesting another ad.');
      setAdDialog('unavailable');
      return;
    }
    setAdBusy(true);
    setAdDialog('watching');
    try {
      const granted = await adSession.current.watch();
      adSession.current = null;
      if (granted) {
        setAdDialog(null);
        setAdMessage('');
        await beginGeneration();
        return;
      }
      setAdMessage('The ad reward was not completed. Watch another ad to generate this video.');
      setAdDialog('unavailable');
    } catch {
      adSession.current = null;
      setAdMessage('The ad could not be completed. Try requesting another one.');
      setAdDialog('unavailable');
    } finally {
      setAdBusy(false);
    }
  }

  function cancelRewardedAd() {
    adRequestId.current += 1;
    adSession.current?.cancel();
    adSession.current = null;
    setAdDialog(null);
    setAdMessage('');
    setAdBusy(false);
  }

  function createAnother() {
    setJob(null);
    setVideoUrl('');
    setMetadata(null);
    setSources([]);
    setExportSettings(null);
    setError('');
    window.localStorage.removeItem('matchcut.lastJobId');
    window.localStorage.removeItem('matchcut.activeJobId');
    window.localStorage.removeItem('matchcut.jobSettings');
  }

  const completed = job?.status === 'complete' && Boolean(videoUrl);
  const progress = Math.max(1, Math.min(100, Number(job?.percent || 0)));
  const previewPhrase = targetWord.trim() || 'Your phrase';
  const previewAspectRatio = videoUrl && metadata?.aspect_ratio ? metadata.aspect_ratio : aspectRatio;
  const selectedEffect = SOUND_EFFECTS.find((effect) => effect.id === sfxId);
  const isGenerating = busy || ['queued', 'running'].includes(job?.status);
  const previewSettingsChanged = Boolean(videoUrl && exportSettings && (
    exportSettings.target_word !== targetWord.trim()
    || exportSettings.aspect_ratio !== aspectRatio
    || exportSettings.highlight_mode !== highlightMode
    || exportSettings.sfx_id !== sfxId
    || exportSettings.duration !== Number(duration)
    || exportSettings.number_of_articles !== Number(articleCount)
    || Number(exportSettings.zoom_level) !== Number(zoomLevel)
    || Number(exportSettings.blur_intensity) !== Number(blurIntensity)
    || exportSettings.sound_style !== soundStyle
    || exportSettings.background_enabled !== backgroundEnabled
  ));

  async function previewSoundEffect() {
    const audio = previewAudio.current;
    if (!audio || !selectedEffect?.asset) return;
    setPreviewError('');
    if (previewingSfx) {
      audio.pause();
      audio.currentTime = 0;
      setPreviewingSfx(false);
      return;
    }
    audio.src = selectedEffect.asset;
    audio.currentTime = 0;
    try {
      await audio.play();
      setPreviewingSfx(true);
    } catch {
      setPreviewError('The selected sound preview could not be played.');
    }
  }

  return (
      <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="MATCH CUT home">
          <span className="brand-mark"><Icon name="mark" size={17} /></span>
          <span>MATCH CUT</span>
        </a>
        <div className="topbar-actions">
          <span className="topbar-note">ONE PHRASE · PAGE BY PAGE</span>
          <button
            type="button"
            className="theme-toggle"
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
            aria-pressed={theme === 'light'}
            onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
          >{theme === 'dark' ? 'LIGHT MODE' : 'DARK MODE'}</button>
          <a className="topbar-feedback" href="mailto:qureshiubaid7860@gmail.com?subject=Match%20Cut%20Feedback">FEEDBACK</a>
        </div>
      </header>

      <section className="intro" id="top">
        <div className="intro-copy">
          <p className="eyebrow">A SIMPLE TEXT MATCH-CUT STUDIO</p>
          <h1>One phrase.<br />A page in motion<span>.</span></h1>
          <p className="intro-description">Choose a word or short phrase and MATCH CUT will follow it across article-style pages, then prepare a downloadable MP4.</p>
        </div>
        <div className="intro-stamp" aria-hidden="true"><span>MP4</span><i>↘</i><small>{aspectRatio}<br />{ASPECT_RATIOS.find((ratio) => ratio.id === aspectRatio).shortLabel}</small></div>
      </section>

      <section className="workspace" aria-label="Match cut video maker">
        <div className="control-column studio-panel">
          <form className="generator-form" onSubmit={generate}>
            <label className="word-label" htmlFor="target-word">What should stay highlighted?</label>
            <input
              id="target-word"
              className={`word-input${wordError ? ' invalid' : ''}`}
              autoComplete="off"
              maxLength={64}
              value={targetWord}
              onChange={(event) => { setTargetWord(event.target.value); setWordError(''); }}
              placeholder="Try “NASA” or “Anything”"
              aria-describedby={wordError ? 'word-error' : 'word-help'}
              aria-invalid={Boolean(wordError)}
            />
            {wordError ? <p className="field-error" id="word-error">{wordError}</p> : <p className="field-help" id="word-help">One word or a short phrase, up to 4 words.</p>}

            <div className="settings-fields primary-settings">
              <label htmlFor="aspect-ratio">Aspect ratio
                <select id="aspect-ratio" value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)}>
                  {ASPECT_RATIOS.map((ratio) => <option value={ratio.id} key={ratio.id}>{ratio.label}</option>)}
                </select>
              </label>
              <label htmlFor="duration">Final video length
                <select id="duration" value={duration} onChange={(event) => setDuration(event.target.value)}>
                  {[6 , 12 , 24 ].map((seconds) => <option value={seconds} key={seconds}>{seconds / 2} seconds</option>)}
                </select>
              </label>
              <label className="mode-field" htmlFor="highlight-mode">Modes
                <select id="highlight-mode" aria-describedby="mode-help" value={highlightMode} onChange={(event) => setHighlightMode(event.target.value)}>
                  {HIGHLIGHT_MODES.map((mode) => <option value={mode.id} key={mode.id}>{mode.label}</option>)}
                </select>
                <span className="field-help" id="mode-help">Default adds a full yellow marker, zoom, and subtle shake; choose 9:16 for the portrait sample look. Moving modes animate the marker or underline.</span>
              </label>
            </div>

            <details className="optional-settings">
              <summary><span>Sound &amp; page options</span><Icon name="down" size={15} /></summary>
              <div className="settings-fields">
                <label htmlFor="article-count">Pages in your video
                  <select id="article-count" value={articleCount} onChange={(event) => setArticleCount(event.target.value)}>
                    {PAGE_COUNTS.map((count) => <option value={count} key={count}>{count} pages</option>)}
                  </select>
                  <span className="field-help">Each page appears once. More pages make cuts faster within the same video length.</span>
                </label>
                <div className="range-field">
                  <label className="range-control-label" htmlFor="zoom-level"><span>Zoom level</span><output htmlFor="zoom-level">{zoomLevel.toFixed(1)}×</output></label>
                  <input
                    id="zoom-level"
                    className="range-slider"
                    type="range"
                    min="1"
                    max="2"
                    step="0.1"
                    value={zoomLevel}
                    onChange={(event) => setZoomLevel(Number(event.target.value))}
                    aria-describedby="zoom-help"
                  />
                  <div className="range-extents" aria-hidden="true"><span>1.0×</span><span>2.0×</span></div>
                  <span className="field-help" id="zoom-help">Higher values make the highlighted phrase larger in the frame.</span>
                </div>
                <div className="range-field">
                  <label className="range-control-label" htmlFor="blur-intensity"><span>Blur around phrase</span><output htmlFor="blur-intensity">{blurIntensity.toFixed(1)}×</output></label>
                  <input
                    id="blur-intensity"
                    className="range-slider"
                    type="range"
                    min="0"
                    max="2.5"
                    step="0.1"
                    value={blurIntensity}
                    onChange={(event) => setBlurIntensity(Number(event.target.value))}
                    aria-describedby="blur-help"
                  />
                  <div className="range-extents" aria-hidden="true"><span>0×</span><span>2.5×</span></div>
                  <span className="field-help" id="blur-help">Adds a soft focus falloff around the word; 0× keeps the page sharp.</span>
                </div>
                <label htmlFor="sound-style">Background ambience
                  <select id="sound-style" value={soundStyle} onChange={(event) => setSoundStyle(event.target.value)} disabled={!backgroundEnabled}>
                    <option value="paper">Paper shuffle</option>
                    <option value="documentary">Documentary</option>
                    <option value="cinematic">Cinematic</option>
                    <option value="marker">Marker</option>
                    <option value="impact">Impact</option>
                    <option value="minimal">Minimal</option>
                  </select>
                </label>
                <label className="ambience-toggle">
                  <input type="checkbox" checked={backgroundEnabled} onChange={(event) => setBackgroundEnabled(event.target.checked)} />
                  Mix background ambience under the selected effect
                </label>
                <div className="sfx-field">
                  <label htmlFor="sfx-id">Page-change sound</label>
                  <div className="sfx-control">
                    <select id="sfx-id" value={sfxId} onChange={(event) => setSfxId(event.target.value)}>
                      {SOUND_EFFECTS.map((effect) => <option value={effect.id} key={effect.id}>{effect.label}</option>)}
                    </select>
                    <button
                      className="sfx-preview-button"
                      type="button"
                      onClick={previewSoundEffect}
                      disabled={!selectedEffect?.asset}
                      aria-label={previewingSfx ? 'Stop sound-effect preview' : 'Preview selected sound effect'}
                    >{previewingSfx ? 'STOP' : 'PLAY'}</button>
                  </div>
                  <audio ref={previewAudio} onEnded={() => setPreviewingSfx(false)} onError={() => { setPreviewingSfx(false); setPreviewError('The selected sound preview could not be loaded.'); }} />
                  {previewError && <span className="sfx-preview-error" role="status">{previewError}</span>}
                </div>
              </div>
            </details>

            {!completed && <button className="generate-button" type="submit" disabled={busy || adBusy || isGenerating || !targetWord.trim()}>
              <span>{isGenerating ? 'Generating...' : adBusy ? 'Checking ad availability…' : 'Generate Match Cut'}</span>
              <span className={`button-icon${busy ? ' spinning' : ''}`}><Icon name={busy ? 'spinner' : 'arrow'} size={19} /></span>
            </button>}
            <p className="service-note">{serviceNote}</p>
          </form>

          {busy && <div className="progress-note" role="status" aria-live="polite">
            <div className="progress-copy"><Icon name="spinner" size={16} /><span>{progressCopy(job)}</span><b>{progress}%</b></div>
            <div className="progress-track" role="progressbar" aria-valuenow={progress} aria-valuemin="0" aria-valuemax="100"><span style={{ width: `${progress}%` }} /></div>
          </div>}
          {error && <div className="error-banner" role="alert"><span className="error-mark">!</span><div><b>We couldn’t generate that video.</b><p>{error || 'Please try again.'}</p></div></div>}
        </div>

        <div className="output-column studio-panel">
          <div className="output-heading">
            <div><span className="field-kicker">YOUR VIDEO</span><h2>{completed ? 'Ready to watch.' : 'Your result will appear here.'}</h2></div>
            <span className="format-tag">{previewAspectRatio}&nbsp; · &nbsp;MP4</span>
          </div>
          <div className={`video-stage${videoUrl ? ' has-video' : ''}`} data-ratio={previewAspectRatio} style={{ aspectRatio: previewAspectRatio.replace(':', ' / ') }}>
            {videoUrl ? <>
              <div className="playback-composition" ref={compositionRef}>
                <video ref={videoRef} key={videoUrl} src={mediaUrl(videoUrl)} controls playsInline preload="metadata" />
              </div>
            </> : (
              <div className="paper-preview" data-mode={highlightMode} aria-label="Preview of the warm paper and yellow phrase highlight">
                <div className="paper-topline"><span>MATCH CUT</span><span>PRINTED PAGE</span></div>
                <span className="paper-kicker">A WORD TO FOLLOW</span>
                <p className="paper-copy faded">A page turns. The story changes.</p>
                <p className="paper-copy focus-line">Then <mark data-mode={highlightMode}>{previewPhrase}</mark> comes into focus.</p>
                <p className="paper-copy">The same phrase stays clear as the pages move.</p>
                <div className="paper-columns" aria-hidden="true"><i /><i /><i /></div>
                <span className="paper-page-number">01 / {String(articleCount).padStart(2, '0')}</span>
              </div>
            )}
          </div>
          <div className="output-meta">
            <span>{metadata ? `${metadata.duration}s MP4 · ${metadata.article_count} pages · ${metadata.aspect_ratio || aspectRatio}` : `${Number(duration) / 2} seconds · ${aspectRatio} video`}</span>
            {videoUrl && job?.id ? <a className="download-link" href={mediaUrl(`/api/download/${encodeURIComponent(job.id)}`)} download={`${(job.target_word || targetWord || 'match-cut').trim().replace(/[^a-z0-9_-]+/gi, '-')}.mp4`}><Icon name="download" size={15} /> DOWNLOAD MP4</a> : <span>H.264 · AAC SOUND</span>}
          </div>
          {previewSettingsChanged && <div className="settings-changed-note" role="status">
            <span>Settings changed. This preview is the previous export. Create another video to apply your current choices.</span>
          </div>}
          {completed && <button type="button" className="create-another-button" onClick={createAnother}>Create Another</button>}
          {completed && metadata && <p className="result-note"><Icon name="check" size={14} /> Highlighted phrase: <strong>{job?.target_word || targetWord}</strong></p>}

          {sources.length > 0 && <details className="sources-details">
            <summary><span>Sources and attribution <small>({sources.length})</small></span><Icon name="down" size={15} /></summary>
            <p className="sources-intro">Public article pages link to their originals. Any generated pages are clearly labeled.</p>
            <div className="source-list">
              {sources.map((source, index) => {
                const isGenerated = source.generated || source.source_kind === 'fictional_fallback';
                const url = source.thumbnail_url ? mediaUrl(source.thumbnail_url.startsWith('/') ? source.thumbnail_url : `/media/${source.thumbnail_url}`) : '';
                return <article className="source-row" key={source.article_id || source.id || `${index}-${source.headline}`}>
                  {url && <img src={url} alt="Article page excerpt" loading="lazy" />}
                  <div className="source-copy">
                    <span className={`source-badge${isGenerated ? ' fictional' : ''}`}>{isGenerated ? 'FICTIONAL PAGE' : 'PUBLIC ARTICLE'}</span>
                    <h3>{source.headline || source.publication || source.filename || `Article page ${index + 1}`}</h3>
                    <p>{isGenerated ? 'Generated editorial page · not a real article' : `${source.publication || sourceHost(source.source_url)} · reconstructed excerpt`}</p>
                    {source.source_url && <a href={source.source_url} target="_blank" rel="noreferrer">Open original article <Icon name="external" size={12} /></a>}
                  </div>
                </article>;
              })}
            </div>
          </details>}
        </div>
      </section>

      <nav className="content-nav" aria-label="Explore MATCH CUT">
        <span>EXPLORE THE STUDIO</span>
        <a href="#examples">Example videos</a>
        <a href="#workflow">How it works</a>
        <a href="#formats-title">Video formats</a>
        <a href="#faq">FAQ</a>
      </nav>

      {adDialog && (
        <div className="modal-backdrop" onClick={adDialog === 'ready' || adDialog === 'unavailable' ? cancelRewardedAd : undefined}>
          <div className="feedback-modal rewarded-modal" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="reward-modal-title">
            <div className="feedback-modal-header">
              <div>
                <span className="feedback-kicker">REWARDED AD REQUIRED</span>
                <h3 id="reward-modal-title">{adDialog === 'unavailable' ? 'Ad currently unavailable' : 'Watch an ad to generate your video'}</h3>
              </div>
            </div>
            {adDialog === 'watching' ? (
              <p className="rewarded-modal-copy">Finish the ad and close it when prompted. Your video starts after Google confirms the reward.</p>
            ) : adDialog === 'unavailable' ? (
              <>
                <p className="rewarded-modal-copy">{adMessage || 'A completed rewarded ad is needed before generation can start.'} Try again when an ad is available.</p>
                <div className="rewarded-modal-actions">
                  <button type="button" className="submit-feedback-button" onClick={() => generate()} disabled={adBusy}>{adBusy ? 'Checking…' : 'Try Again'}</button>
                  <button type="button" className="reward-cancel-button" onClick={cancelRewardedAd}>Cancel</button>
                </div>
              </>
            ) : (
              <>
                <p className="rewarded-modal-copy">One completed rewarded ad is required for each video. Ad length varies; generation starts after Google confirms the reward.</p>
                <div className="rewarded-modal-actions">
                  <button type="button" className="submit-feedback-button" onClick={watchRewardedAd} disabled={adBusy}>{adBusy ? 'Opening ad…' : 'Watch Ad'}</button>
                  <button type="button" className="reward-cancel-button" onClick={cancelRewardedAd}>Cancel</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      <section className="content-section examples-section" id="examples" aria-labelledby="examples-title">
        <div className="section-heading section-heading-wide">
          <div>
            <p className="eyebrow">MADE WITH MATCH CUT</p>
            <h2 id="examples-title">Three phrases, three page trails.</h2>
          </div>
          <p className="section-deck">Playable example renders show the newspaper-style text match cut in motion. These are creative demonstrations, not factual news reports.</p>
        </div>
        <div className="demo-grid">
          <article className="demo-card">
            <div className="demo-card-top"><span>01 / EXAMPLE RENDER</span><span className="demo-topic">ARTIFICIAL INTELLIGENCE</span><span className="demo-mode">DEFAULT HIGHLIGHT</span></div>
            <DemoVideo title="Artificial Intelligence" video="/demo-videos/artificial-intelligence.mp4" poster="/demo-videos/artificial-intelligence.jpg" mode="Default Highlight" />
            <div className="demo-card-copy"><span className="phrase-chip">Artificial Intelligence</span><h3>One phrase, page after page.</h3><p>See how one phrase can stay in focus as the article pages shift around it.</p></div>
          </article>
          <article className="demo-card">
            <div className="demo-card-top"><span>02 / EXAMPLE RENDER</span><span className="demo-topic">AI AGENTS &amp; AUTOMATION</span><span className="demo-mode">MOVING HIGHLIGHT</span></div>
            <DemoVideo title="AI Agents" video="/demo-videos/ai-agents.mp4" poster="/demo-videos/ai-agents.jpg" mode="Moving Highlight" />
            <div className="demo-card-copy"><span className="phrase-chip">AI Agents</span><h3>Follow an idea through the print.</h3><p>A short newspaper-style cut about software that can take a goal and work through steps.</p></div>
          </article>
          <article className="demo-card">
            <div className="demo-card-top"><span>03 / EXAMPLE RENDER</span><span className="demo-topic">CLIMATE &amp; TECHNOLOGY</span><span className="demo-mode">MOVING UNDERLINE</span></div>
            <DemoVideo title="Clean Energy" video="/demo-videos/clean-energy.mp4" poster="/demo-videos/clean-energy.jpg" mode="Moving Underline" />
            <div className="demo-card-copy"><span className="phrase-chip">Clean Energy</span><h3>A moving phrase in a changing layout.</h3><p>Follow a phrase through a changing set of print-inspired editorial pages.</p></div>
          </article>
        </div>
      </section>

      <section className="content-section how-section" id="workflow" aria-labelledby="workflow-title">
        <div className="section-heading">
          <p className="eyebrow">HOW IT WORKS</p>
          <h2 id="workflow-title">From a phrase to a finished cut.</h2>
          <p className="section-deck">Start with the words you want to carry through the video. The controls stay simple, with extra options when you need them.</p>
        </div>
        <div className="steps-grid">
          <article className="info-card"><span>1</span><h3>Enter a phrase</h3><p>Type a word or a short phrase of up to four words. A clear, distinctive phrase is a useful place to start.</p></article>
          <article className="info-card"><span>2</span><h3>Choose the look</h3><p>Set the video length and shape, then choose a page count, yellow highlight mode, and sound options.</p></article>
          <article className="info-card"><span>3</span><h3>Preview your MP4</h3><p>Watch the result, review the source notes when available, and use Download MP4 to save your video.</p></article>
        </div>
      </section>

      <section className="content-section formats-section" aria-labelledby="formats-title">
        <div className="section-heading section-heading-wide">
          <div><p className="eyebrow">PICK YOUR FRAME</p><h2 id="formats-title">Five formats for different canvases.</h2></div>
          <p className="section-deck">Choose the aspect ratio that fits where you plan to use the video. The vertical ratio is a useful starting point for Shorts, Reels, or TikTok; check each platform’s current framing rules before posting.</p>
        </div>
        <div className="format-grid">
          <article className="format-card format-vertical"><span className="format-shape" aria-hidden="true"><i /></span><div><b>9:16</b><h3>Vertical</h3><p>Portrait-first viewing</p><small>Shorts · Reels · TikTok</small></div></article>
          <article className="format-card"><span className="format-shape square" aria-hidden="true"><i /></span><div><b>1:1</b><h3>Square</h3><p>Balanced, compact frame</p></div></article>
          <article className="format-card"><span className="format-shape landscape" aria-hidden="true"><i /></span><div><b>16:9</b><h3>Landscape</h3><p>Wide-screen composition</p></div></article>
          <article className="format-card"><span className="format-shape portrait" aria-hidden="true"><i /></span><div><b>4:5</b><h3>Portrait</h3><p>Tall feed composition</p></div></article>
          <article className="format-card"><span className="format-shape standard" aria-hidden="true"><i /></span><div><b>4:3</b><h3>Standard</h3><p>Classic screen proportions</p></div></article>
        </div>
      </section>

      <section className="content-section effect-section" aria-labelledby="effect-title">
        <div className="effect-copy">
          <p className="eyebrow">THE TEXT MATCH-CUT EFFECT</p>
          <h2 id="effect-title">One phrase becomes the visual anchor.</h2>
          <p>A text match cut keeps a chosen word or short phrase in view while the surrounding newspaper-style page changes. The repeated position and yellow mark make the changing layouts feel connected, like a newspaper clipping effect built around a word highlight video.</p>
          <p>MATCH CUT can search public article pages when they are available. If it needs to fill selected page slots, generated editorial pages are marked as fictional in the source notes. The result is a documentary-style text animation; it does not turn generated copy into reporting.</p>
        </div>
        <div className="effect-aside" aria-label="Three parts of the text match cut effect">
          <div><span>01</span><b>Phrase</b><small>Your selected words set the focus.</small></div>
          <div><span>02</span><b>Highlight</b><small>Pick the default, moving highlight, or underline.</small></div>
          <div><span>03</span><b>Page change</b><small>Editorial layouts shift around that anchor.</small></div>
        </div>
      </section>

      <section className="content-section comparison-section" aria-labelledby="comparison-title">
        <div className="section-heading section-heading-wide">
          <div><p className="eyebrow">A WORKFLOW COMPARISON</p><h2 id="comparison-title">Different ways to build a text-led video.</h2></div>
          <p className="section-deck">A practical look at where each workflow begins and what the creator handles.</p>
        </div>
        <p className="comparison-scroll-hint">Swipe sideways to compare all columns <span aria-hidden="true">→</span></p>
        <div className="comparison-table-wrap" tabIndex="0" role="region" aria-label="Scrollable comparison of video workflows">
          <table className="comparison-table">
            <thead><tr><th scope="col">Workflow</th><th scope="col">Starting point</th><th scope="col">Phrase alignment</th><th scope="col">Pacing &amp; pages</th><th scope="col">Output</th></tr></thead>
            <tbody>
              <tr className="matchcut-row"><th scope="row"><span className="table-mark">MATCH CUT</span></th><td>Enter a phrase and choose a format.</td><td>The phrase is the visual focus across the selected page sequence.</td><td>Select a page count and video length; pages are paced within that duration.</td><td>Preview and download the generated MP4 with source notes when available.</td></tr>
              <tr><th scope="row">Manual timeline editing</th><td>Import or create page visuals and assemble a timeline.</td><td>Place and align the phrase by hand on each page.</td><td>Set each page’s duration and transition on the timeline.</td><td>Export with the controls available in the chosen editing software.</td></tr>
              <tr><th scope="row">General-purpose template editor</th><td>Start from a template or a blank project.</td><td>Text and timing controls vary by editor and template.</td><td>Adjust scenes and durations using the available template controls.</td><td>Export options depend on the editor, plan, and project settings.</td></tr>
            </tbody>
          </table>
        </div>
        <p className="comparison-note">This compares broad workflows, not prices or every tool’s capabilities.</p>
      </section>

      <section className="content-section faq-section" id="faq" aria-labelledby="faq-title">
        <div className="section-heading section-heading-wide">
          <div><p className="eyebrow">TEXT MATCH CUT FAQ</p><h2 id="faq-title">Answers before you make a cut.</h2></div>
          <p className="section-deck">A few details about phrases, pages, sound, and the MP4 you create.</p>
        </div>
        <div className="faq-list">
          <details open><summary>How long can my phrase be?</summary><p>Use one word or a short phrase of up to four words. Clear spelling and distinctive wording can make a phrase easier to follow.</p></details>
          <details><summary>How many pages will my video use?</summary><p>Choose 6, 12, 24, 36, 48, 60, or 72 pages. The selected page count is used, with each page appearing once in the video.</p></details>
          <details><summary>Are generated pages real articles?</summary><p>No. Generated editorial pages are fictional and labeled in the source notes. They are used when the selected sequence needs pages beyond those found publicly.</p></details>
          <details><summary>Will every page come from a public source?</summary><p>Public article pages are used when available, but a matching set may not exist for every phrase. Check the source notes to see which pages link to originals and which are fictional.</p></details>
          <details><summary>Which video ratios can I choose?</summary><p>Choose 9:16 vertical, 1:1 square, 16:9 landscape, 4:5 portrait, or 4:3 standard before generating.</p></details>
          <details><summary>What highlight modes are available?</summary><p>There are three options: Default Highlight, Moving Highlight, and Moving Underline. The selection is applied when you generate a video.</p></details>
          <details><summary>Can I choose the sound?</summary><p>Yes. Pick a page-change sound and optionally mix a background ambience. You can preview the selected page-change effect in the form.</p></details>
          <details><summary>How do I download the MP4?</summary><p>When the result is ready, select Download MP4 below the preview. Your browser will start downloading the generated video.</p></details>
          <details><summary>Can I edit the downloaded video?</summary><p>The MP4 can be opened in video editing software that supports MP4 files. The downloaded file is a rendered video, so individual page elements are not separate editable layers.</p></details>
        </div>
      </section>

      <section className="final-cta" aria-labelledby="final-cta-title">
        <div><p className="eyebrow">READY WHEN YOU ARE</p><h2 id="final-cta-title">Choose a phrase. Watch the pages move.</h2><p>Start with a word or short phrase and make a newspaper-style text match cut.</p></div>
        <a href="#target-word" className="final-cta-link">MAKE A MATCH CUT <Icon name="arrow" size={17} /></a>
      </section>

      {feedbackOpen && (
        <div className="modal-backdrop" onClick={() => setFeedbackOpen(false)}>
          <div className="feedback-modal" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="feedback-modal-title">
            <div className="feedback-modal-header">
              <div>
                <span className="feedback-kicker">SEND FEEDBACK</span>
                <h3 id="feedback-modal-title">Help us improve Match Cut</h3>
              </div>
              <button type="button" className="modal-close" onClick={() => setFeedbackOpen(false)} aria-label="Close feedback form">×</button>
            </div>
            <form className="feedback-form" onSubmit={async (event) => {
              event.preventDefault();
              setFeedbackStatus('');
              try {
                const response = await apiRequest('/api/feedback', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    rating: Number(feedbackForm.rating),
                    issue_type: feedbackForm.issueType,
                    message: feedbackForm.message.trim(),
                  }),
                });
                setFeedbackStatus(response.message || 'Thanks for your feedback.');
                setFeedbackForm({ rating: '5', issueType: 'Feature request', message: '' });
                setTimeout(() => setFeedbackOpen(false), 1200);
              } catch (caught) {
                setFeedbackStatus(caught.message || 'Could not send your feedback.');
              }
            }}>
              <label>
                Rating
                <select value={feedbackForm.rating} onChange={(event) => setFeedbackForm((current) => ({ ...current, rating: event.target.value }))}>
                  <option value="5">5 - Excellent</option>
                  <option value="4">4 - Good</option>
                  <option value="3">3 - Mixed</option>
                  <option value="2">2 - Poor</option>
                  <option value="1">1 - Very poor</option>
                </select>
              </label>
              <label>
                Issue type
                <select value={feedbackForm.issueType} onChange={(event) => setFeedbackForm((current) => ({ ...current, issueType: event.target.value }))}>
                  <option value="Generation failed">Generation failed</option>
                  <option value="Poor visual match">Poor visual match</option>
                  <option value="Animation issue">Animation issue</option>
                  <option value="Sound issue">Sound issue</option>
                  <option value="Mobile issue">Mobile issue</option>
                  <option value="UI issue">UI issue</option>
                  <option value="Feature request">Feature request</option>
                  <option value="Other">Other</option>
                </select>
              </label>
              <label>
                Optional feedback
                <textarea rows="5" value={feedbackForm.message} onChange={(event) => setFeedbackForm((current) => ({ ...current, message: event.target.value }))} placeholder="Tell us what went wrong or what would make the tool more useful." />
              </label>
              {feedbackStatus && <p className="feedback-status" role="status">{feedbackStatus}</p>}
              <button type="submit" className="submit-feedback-button">SEND FEEDBACK</button>
            </form>
          </div>
        </div>
      )}

      <footer className="page-footer">
        <div className="footer-main">
          <div className="footer-brand-block">
            <a className="footer-brand" href="#top"><span className="brand-mark"><Icon name="mark" size={17} /></span><span>MATCH CUT</span></a>
            <p>One phrase. A changing page.</p>
          </div>
          <nav className="footer-guides" aria-label="Text match cut guides">
            <h2>Explore</h2>
            <div className="footer-guide-links">
              <a href="/text-match-cut-generator/">Text match cut generator</a>
              <a href="/newspaper-highlight-generator/">Newspaper highlight video maker</a>
              <a href="/newspaper-word-highlight/">Newspaper word highlight</a>
              <a href="/newspaper-animation/">Newspaper animation</a>
              <a href="/documentary-text-animation/">Documentary text animation</a>
            </div>
          </nav>
          <div className="footer-contact">
            <h2>Get in touch</h2>
            <button type="button" className="footer-feedback-button" onClick={() => setFeedbackOpen(true)}>Send feedback</button>
            <a href="mailto:qureshiubaid7860@gmail.com?subject=Match%20Cut%20Query">Ask a question <Icon name="arrow" size={13} /></a>
          </div>
        </div>
        <div className="footer-base">
          <span>© 2026 Ubaid Qureshi</span>
          <a href="#top">Back to top ↑</a>
        </div>
      </footer>
    </main>
  );
}

export default App;
