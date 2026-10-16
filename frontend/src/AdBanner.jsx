import { useEffect, useRef } from 'react';

const NATIVE_SCRIPT_URL = 'https://bauval.org/21/38665ed3aaafb452e7c929fc3a6fcaed';
const NATIVE_CONTAINER_ID = 'container-38665ed3aaafb452e7c929fc3a6fcaed';
const LEADERBOARD_KEY = '2f62314187284ae7163e951aa8cbf52f';
const LEADERBOARD_SCRIPT_URL = `https://bauval.org/22/${LEADERBOARD_KEY}`;
const OUT_OF_PAGE_SCRIPT_URLS = [
  'https://abscloud.org/1/288a600ee76cace6c347efa76a258495',
  'https://bauval.org/14/1188690258c118fe52d813aaeff2cb2d',
];
let nativeScriptRequested = false;
let leaderboardScriptRequested = false;
let outOfPageScriptsRequested = false;

function AdLabel() {
  return <span className="ad-banner-label">ADVERTISEMENT</span>;
}

export function AdsterraOutOfPageScripts() {
  useEffect(() => {
    if (!import.meta.env.PROD || outOfPageScriptsRequested) return;

    outOfPageScriptsRequested = true;
    OUT_OF_PAGE_SCRIPT_URLS.forEach((src) => {
      const script = document.createElement('script');
      script.async = true;
      script.setAttribute('data-cfasync', 'false');
      script.src = src;
      document.body.appendChild(script);
    });
  }, []);

  return null;
}

export function AdsterraNativeBanner() {
  const adRef = useRef(null);

  useEffect(() => {
    const container = adRef.current;
    const placement = container?.parentElement;
    if (!container || !placement || nativeScriptRequested) return;

    nativeScriptRequested = true;
    const script = document.createElement('script');
    script.async = true;
    script.setAttribute('data-cfasync', 'false');
    script.src = NATIVE_SCRIPT_URL;
    placement.insertBefore(script, container);
  }, []);

  return (
    <aside className="ad-banner ad-banner-native" aria-label="Advertisement">
      <AdLabel />
      <div ref={adRef} id={NATIVE_CONTAINER_ID} className="ad-native-slot" />
    </aside>
  );
}

export function AdsterraLeaderboardBanner() {
  const adRef = useRef(null);

  useEffect(() => {
    const container = adRef.current;
    if (!container || leaderboardScriptRequested || !window.matchMedia('(min-width: 772px)').matches) return;

    leaderboardScriptRequested = true;
    window.atOptions = {
      key: LEADERBOARD_KEY,
      format: 'iframe',
      height: 90,
      width: 728,
      params: {},
    };

    const script = document.createElement('script');
    script.async = true;
    script.setAttribute('data-cfasync', 'false');
    script.src = LEADERBOARD_SCRIPT_URL;
    container.appendChild(script);
  }, []);

  return (
    <aside className="ad-banner ad-banner-leaderboard" aria-label="Advertisement">
      <AdLabel />
      <div ref={adRef} className="ad-leaderboard-slot" />
    </aside>
  );
}
