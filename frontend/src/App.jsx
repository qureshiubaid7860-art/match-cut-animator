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

function App() {
  const [targetWord, setTargetWord] = useState('');
  const [duration, setDuration] = useState('6');
  const [articleCount, setArticleCount] = useState('6');
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
  const [adBusy, setAdBusy] = useState(false);
  const adSession = useRef(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackForm, setFeedbackForm] = useState({ rating: '5', issueType: 'Feature request', message: '' });
  const [feedbackStatus, setFeedbackStatus] = useState('');

  useEffect(() => {
    window.localStorage.setItem('matchcut.aspectRatio', aspectRatio);
  }, [aspectRatio]);

  useEffect(() => {
    window.localStorage.setItem('matchcut.highlightMode', highlightMode);
  }, [highlightMode]);

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
      if (active) setServiceNote(result.auto_search_configured ? 'Public pages are checked first; generated pages are labeled.' : 'Automatic page search is unavailable.');
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
        if (savedSettings.number_of_articles) setArticleCount(String(savedSettings.number_of_articles));
        if (savedSettings.aspect_ratio) setAspectRatio(savedSettings.aspect_ratio);
        if (savedSettings.highlight_mode) setHighlightMode(savedSettings.highlight_mode);
        if (savedSettings.sfx_id) setSfxId(savedSettings.sfx_id);
        if (savedSettings.sound_style) setSoundStyle(savedSettings.sound_style);
        if (typeof savedSettings.background_enabled === 'boolean') setBackgroundEnabled(savedSettings.background_enabled);
        const progress = await apiRequest(`/api/progress/${encodeURIComponent(savedJobId)}`);
        setJob(progress);
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
      setJob(progress);
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
    if (busy || adBusy) return;
    const phrase = targetWord.trim();
    const words = phrase.match(/[^\W_]+/gu) || [];
    if (!phrase || words.length > 4) {
      setWordError('Enter a word or a short phrase of up to 4 words.');
      return;
    }

    setError('');
    if (isRewardedAdGateEnabled()) {
      setAdBusy(true);
      try {
        const result = await rewardedAdService.prepareRewardedAd();
        if (result.available) {
          adSession.current = result;
          setAdDialog('ready');
          return;
        }
        console.info('Rewarded ad unavailable; continuing without an ad:', result.reason);
      } catch (adError) {
        console.info('Rewarded ad setup failed; continuing without an ad:', adError);
      } finally {
        setAdBusy(false);
      }
    }
    await beginGeneration();
  }

  async function watchRewardedAd() {
    if (!adSession.current) {
      setAdDialog('unavailable');
      return;
    }
    setAdDialog('watching');
    try {
      const granted = await adSession.current.watch();
      adSession.current = null;
      if (granted) {
        setAdDialog(null);
        await beginGeneration();
        return;
      }
      setAdDialog('unavailable');
    } catch {
      adSession.current = null;
      setAdDialog('unavailable');
    }
  }

  function cancelRewardedAd() {
    adSession.current?.cancel();
    adSession.current = null;
    setAdDialog(null);
  }

  async function continueWithoutAd() {
    adSession.current?.cancel();
    adSession.current = null;
    setAdDialog(null);
    await beginGeneration();
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
          <span className="topbar-note">A PRINTED PAGE, IN MOTION</span>
          <a className="topbar-feedback" href="mailto:qureshiubaid7860@gmail.com?subject=Match%20Cut%20Feedback">FEEDBACK</a>
        </div>
      </header>

      <section className="intro" id="top">
        <div className="intro-copy">
          <p className="eyebrow">YOUR WORD, ACROSS THE PAGE</p>
          <h1>Make a phrase<br />hold its place<span>.</span></h1>
            <p className="intro-description">Enter a word or short phrase. We’ll keep it pinned in the center while the article fragments change around it.</p>
        </div>
        <div className="intro-stamp" aria-hidden="true"><span>MP4</span><i>↘</i><small>{aspectRatio}<br />{ASPECT_RATIOS.find((ratio) => ratio.id === aspectRatio).shortLabel}</small></div>
      </section>

      <section className="workspace" aria-label="Match cut video maker">
        <div className="control-column">
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
              <label htmlFor="duration">Video length
                <select id="duration" value={duration} onChange={(event) => setDuration(event.target.value)}>
                  {[6 , 12 , 24 ].map((seconds) => <option value={seconds} key={seconds}>{seconds} seconds</option>)}
                </select>
              </label>
              <label className="mode-field" htmlFor="highlight-mode">Modes
                <select id="highlight-mode" aria-describedby="mode-help" value={highlightMode} onChange={(event) => setHighlightMode(event.target.value)}>
                  {HIGHLIGHT_MODES.map((mode) => <option value={mode.id} key={mode.id}>{mode.label}</option>)}
                </select>
                <span className="field-help" id="mode-help">Moving effects draw once from start to end with playback. Pause or seek to control them.</span>
              </label>
              <label htmlFor="sound-style">Background ambience style
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

            <details className="optional-settings">
              <summary><span>More options</span><Icon name="down" size={15} /></summary>
              <div className="settings-fields">
                <label htmlFor="article-count">Pages in your video
                  <select id="article-count" value={articleCount} onChange={(event) => setArticleCount(event.target.value)}>
                    {[ 6, 12, 24, 36].map((count) => <option value={count} key={count}>{count} pages</option>)}
                  </select>
                </label>
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
          <aside className="feedback-callout" id="feedback" aria-label="Feedback and contact">
            <span className="feedback-kicker">FEEDBACK / CONTACT</span>
            <p><a href="mailto:qureshiubaid7860@gmail.com?subject=Match%20Cut%20Feedback">Send feedback</a> <span aria-hidden="true">·</span> <a href="mailto:qureshiubaid7860@gmail.com?subject=Match%20Cut%20Query">Any queries?</a></p>
            <a className="feedback-email" href="mailto:qureshiubaid7860@gmail.com">qureshiubaid7860@gmail.com</a>
          </aside>
        </div>

        <div className="output-column">
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
                <span className="paper-page-number">01 / 06</span>
              </div>
            )}
          </div>
          <div className="output-meta">
            <span>{metadata ? `${metadata.duration}s output (${metadata.requested_duration ?? metadata.duration}s at ${metadata.playback_speed ?? 1}×) · ${metadata.article_count} pages · ${metadata.aspect_ratio || aspectRatio} · ${metadata.sfx_id || 'page_turn'}` : `${duration} seconds · ${aspectRatio} video · 2× playback`}</span>
            {videoUrl ? <a className="download-link" href={mediaUrl(videoUrl)} download><Icon name="download" size={15} /> DOWNLOAD MP4</a> : <span>H.264 · AAC SOUND</span>}
          </div>
          {previewSettingsChanged && <div className="settings-changed-note" role="status">
            <span>Settings changed. This preview is the previous export. Create another video to apply your current choices.</span>
          </div>}
          {completed && <button type="button" className="create-another-button" onClick={createAnother}>Create Another</button>}
          {completed && metadata && <p className="result-note"><Icon name="check" size={14} /> Highlighted phrase: <strong>{job?.target_word || targetWord}</strong></p>}

          {sources.length > 0 && <details className="sources-details">
            <summary><span>Sources and attribution <small>({sources.length})</small></span><Icon name="down" size={15} /></summary>
            <p className="sources-intro">Public article pages link to their original text. Any generated pages are clearly identified as fictional.</p>
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

      {adDialog && (
        <div className="modal-backdrop" onClick={adDialog === 'ready' || adDialog === 'unavailable' ? cancelRewardedAd : undefined}>
          <div className="feedback-modal rewarded-modal" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="reward-modal-title">
            <div className="feedback-modal-header">
              <div>
                <span className="feedback-kicker">OPTIONAL REWARDED AD</span>
                <h3 id="reward-modal-title">{adDialog === 'unavailable' ? 'Ad currently unavailable' : 'Watch a short ad to unlock this generation'}</h3>
              </div>
            </div>
            {adDialog === 'watching' ? (
              <p className="rewarded-modal-copy">The ad is open. This generation will start only if Google confirms the reward.</p>
            ) : adDialog === 'unavailable' ? (
              <>
                <p className="rewarded-modal-copy">No reward was received. You can continue without an ad.</p>
                <div className="rewarded-modal-actions">
                  <button type="button" className="submit-feedback-button" onClick={continueWithoutAd}>Continue Without Ad</button>
                  <button type="button" className="reward-cancel-button" onClick={cancelRewardedAd}>Cancel</button>
                </div>
              </>
            ) : (
              <>
                <p className="rewarded-modal-copy">Watching is optional. If you skip or the ad is unavailable, you can continue without it.</p>
                <div className="rewarded-modal-actions">
                  <button type="button" className="submit-feedback-button" onClick={watchRewardedAd}>Watch Ad</button>
                  <button type="button" className="reward-cancel-button" onClick={cancelRewardedAd}>Cancel</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      <section className="content-section" aria-label="How Match Cut works">
        <div className="section-heading">
          <p className="eyebrow">HOW IT WORKS</p>
          <h2>Turn one repeated word into a clean documentary-style motion sequence.</h2>
        </div>
        <div className="steps-grid">
          <div className="info-card"><span>1</span><h3>Choose your word</h3><p>Enter a word or short phrase that should stay pinned in place while the article pages move around it.</p></div>
          <div className="info-card"><span>2</span><h3>Pick a mode</h3><p>Use the default highlight, the moving highlight, or the moving underline to match the pace of your project.</p></div>
          <div className="info-card"><span>3</span><h3>Generate</h3><p>Our workflow matches the phrase to the strongest frames and prepares an MP4 you can preview and download.</p></div>
          <div className="info-card"><span>4</span><h3>Export</h3><p>Review the result, share it, or reuse the file in a presentation, social post, or motion project.</p></div>
        </div>
      </section>

      <section className="content-section alt-section" aria-label="Match Cut features">
        <div className="section-heading">
          <p className="eyebrow">FEATURES</p>
          <h2>Built for quick page-based motion with a polished documentary feel.</h2>
        </div>
        <div className="feature-grid">
          <div className="feature-item">Automatic visual matching</div>
          <div className="feature-item">Documentary-style motion</div>
          <div className="feature-item">Smooth zoom and camera drift</div>
          <div className="feature-item">Word-focused animation</div>
          <div className="feature-item">Timeline/highlighter effect</div>
          <div className="feature-item">Underline animation</div>
          <div className="feature-item">Multiple modes</div>
          <div className="feature-item">Sound effects</div>
          <div className="feature-item">Fast browser workflow</div>
        </div>
      </section>

      <section className="content-section faq-section" aria-label="Frequently asked questions">
        <div className="section-heading">
          <p className="eyebrow">FAQ</p>
          <h2>Answers for real-world usage and production timing.</h2>
        </div>
        <div className="faq-list">
          <details open><summary>What is a match cut?</summary><p>It is a motion edit that keeps a repeated word or phrase visually consistent while the surrounding frames change around it.</p></details>
          <details><summary>How does the Match Cut Animator work?</summary><p>It finds the best matching article frames, locks the target word into a consistent position, and renders an MP4 with timing, motion, and sound.</p></details>
          <details><summary>Do I need to upload images?</summary><p>Yes, for full article-based generation you upload the source pages and the app analyzes them in-browser and on the backend.</p></details>
          <details><summary>How long does generation take?</summary><p>It varies with the page count, OCR work, and rendering time. Shorter videos can finish in a few moments; heavier projects take longer.</p></details>
          <details><summary>Can I use the generated video in my projects?</summary><p>Yes, if you have the rights to the source material and the generated output is used in line with your project requirements.</p></details>
          <details><summary>Does it work on mobile?</summary><p>The interface is responsive, but the heaviest processing still depends on device power and browser capabilities.</p></details>
          <details><summary>Why can generation take longer sometimes?</summary><p>Render time depends on the selected video length, frame count, OCR fidelity, and whether the background audio or final export stage needs extra processing.</p></details>
          <details><summary>What happens if generation fails?</summary><p>You’ll see a clear error state with the cause and can retry once the issue is corrected, such as a weak OCR match or an unavailable service.</p></details>
          <details><summary>Why might I see a rewarded ad?</summary><p>Rewarded ads are optional and only appear when they are enabled and configured for a production deployment. If no ad is available, generation can continue without one.</p></details>
          <details><summary>Can I generate another video after completing one generation?</summary><p>Yes. Choose Create Another, adjust your options if needed, and start a fresh generation. A configured rewarded ad is optional and can be skipped when unavailable.</p></details>
        </div>
      </section>

      <aside className="feedback-callout content-feedback" id="feedback-form" aria-label="Feedback form">
        <span className="feedback-kicker">FEEDBACK</span>
        <div className="feedback-callout-row">
          <p>Let us know how the tool feels in the real world.</p>
          <button type="button" className="inline-feedback-button" onClick={() => setFeedbackOpen(true)}>OPEN FORM</button>
        </div>
      </aside>

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
        <div className="page-footer-brand">
          <span>© 2026 Ubaid Qureshi</span>
        </div>
        <div className="page-footer-links">
          <button type="button" className="footer-feedback-button" onClick={() => setFeedbackOpen(true)}>Send feedback</button>
          <span aria-hidden="true">·</span>
          <a href="mailto:qureshiubaid7860@gmail.com?subject=Match%20Cut%20Query">Any queries?</a>
        </div>
      </footer>
    </main>
  );
}

export default App;
