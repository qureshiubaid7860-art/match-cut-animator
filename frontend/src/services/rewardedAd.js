const ADS_ENABLED = import.meta.env.VITE_REWARDED_ADS_ENABLED === 'true';
const AD_UNIT_PATH = import.meta.env.VITE_REWARDED_AD_UNIT_PATH || '';
const PREPARE_TIMEOUT_MS = 8000;
const WATCH_TIMEOUT_MS = 120000;

function isLocalDevelopment() {
  if (typeof window === 'undefined') return true;
  return ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);
}

export function isRewardedAdGateEnabled() {
  return ADS_ENABLED && Boolean(AD_UNIT_PATH) && !isLocalDevelopment();
}

function loadGoogleTag() {
  if (typeof window === 'undefined') return Promise.resolve(false);
  if (window.googletag?.apiReady) return Promise.resolve(true);

  return new Promise((resolve) => {
    let settled = false;
    const finish = (loaded) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeoutId);
      resolve(loaded);
    };
    const timeoutId = window.setTimeout(() => finish(false), PREPARE_TIMEOUT_MS);
    const script = document.createElement('script');
    script.src = 'https://securepubads.g.doubleclick.net/tag/js/gpt.js';
    script.async = true;
    script.onload = () => finish(Boolean(window.googletag));
    script.onerror = () => finish(false);
    document.head.appendChild(script);
  });
}

export async function prepareRewardedAd() {
  if (!isRewardedAdGateEnabled()) {
    return { available: false, reason: 'Rewarded ads are disabled or not configured.' };
  }

  if (!await loadGoogleTag()) {
    return { available: false, reason: 'The Google ad script could not be loaded.' };
  }

  return new Promise((resolve) => {
    const googletag = window.googletag;
    const pubads = googletag.pubads();
    let settled = false;
    let rewardedSlot;
    let readyEvent;
    let readyTimeout;
    let watchGrantedHandler;
    let watchClosedHandler;

    const removeListeners = () => {
      window.clearTimeout(readyTimeout);
      pubads.removeEventListener('rewardedSlotReady', onReady);
      if (watchGrantedHandler) pubads.removeEventListener('rewardedSlotGranted', watchGrantedHandler);
      if (watchClosedHandler) pubads.removeEventListener('rewardedSlotClosed', watchClosedHandler);
    };

    const finishUnavailable = (reason) => {
      if (settled) return;
      settled = true;
      removeListeners();
      resolve({ available: false, reason });
    };

    const onReady = (event) => {
      if (event.slot !== rewardedSlot || settled) return;
      settled = true;
      window.clearTimeout(readyTimeout);
      readyEvent = event;
      pubads.removeEventListener('rewardedSlotReady', onReady);

      resolve({
        available: true,
        watch: () => new Promise((complete) => {
          let watchSettled = false;
          const finishWatch = (granted) => {
            if (watchSettled) return;
            watchSettled = true;
            window.clearTimeout(watchTimeout);
            removeListeners();
            complete(granted);
          };
          watchGrantedHandler = (grantEvent) => {
            if (grantEvent.slot === rewardedSlot) finishWatch(true);
          };
          watchClosedHandler = (closedEvent) => {
            if (closedEvent.slot === rewardedSlot) finishWatch(false);
          };

          pubads.addEventListener('rewardedSlotGranted', watchGrantedHandler);
          pubads.addEventListener('rewardedSlotClosed', watchClosedHandler);
          const watchTimeout = window.setTimeout(() => finishWatch(false), WATCH_TIMEOUT_MS);
          try {
            readyEvent.makeRewardedVisible();
          } catch {
            finishWatch(false);
          }
        }),
        cancel: () => {
          removeListeners();
          if (rewardedSlot) googletag.destroySlots([rewardedSlot]);
        },
      });
    };

    pubads.addEventListener('rewardedSlotReady', onReady);

    readyTimeout = window.setTimeout(
      () => finishUnavailable('No rewarded ad became available.'),
      PREPARE_TIMEOUT_MS,
    );

    googletag.cmd = googletag.cmd || [];
    googletag.cmd.push(() => {
      try {
        const format = googletag.enums?.OutOfPageFormat?.REWARDED;
        if (!format) {
          finishUnavailable('The Google Publisher Tag rewarded format is unavailable.');
          return;
        }
        rewardedSlot = googletag.defineOutOfPageSlot(AD_UNIT_PATH, format);
        if (!rewardedSlot) {
          finishUnavailable('No rewarded ad slot is available.');
          return;
        }
        rewardedSlot.addService(pubads);
        googletag.enableServices();
        googletag.display(rewardedSlot);
      } catch {
        finishUnavailable('The rewarded ad could not be prepared.');
      }
    });
  });
}

export default { isRewardedAdGateEnabled, prepareRewardedAd };
