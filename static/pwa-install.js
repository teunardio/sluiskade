/*
 * PWA install prompt - werkt voor Chrome/Edge/Android (beforeinstallprompt
 * API) en geeft een instructie voor iOS Safari (geen API beschikbaar daar).
 *
 * Verwacht een container met id="pwaInstallCard" in de pagina. Verbergt
 * 'm automatisch als de site al als app draait (display-mode: standalone).
 *
 * Persistente dismiss via localStorage zodat we niet eeuwig blijven nudgen.
 */
(function () {
  const card = document.getElementById('pwaInstallCard');
  if (!card) return;

  const STORAGE_KEY = 'sluiskade-pwa-dismissed';

  // Al geïnstalleerd? Verberg.
  if (window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone === true) {
    card.hidden = true;
    return;
  }

  // Gebruiker eerder weggeklikt en dat is recent (< 30 dagen)? Stil houden.
  const dismissed = parseInt(localStorage.getItem(STORAGE_KEY) || '0', 10);
  if (dismissed && Date.now() - dismissed < 30 * 24 * 60 * 60 * 1000) {
    card.hidden = true;
    return;
  }

  const installBtn = card.querySelector('[data-pwa-install]');
  const dismissBtn = card.querySelector('[data-pwa-dismiss]');
  const iosHint = card.querySelector('[data-pwa-ios-hint]');

  // iOS Safari detection
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  const isSafari = /Safari/.test(navigator.userAgent) && !/Chrome|CriOS|FxiOS/.test(navigator.userAgent);

  let deferredPrompt = null;

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    card.hidden = false;
    if (installBtn) installBtn.hidden = false;
    if (iosHint) iosHint.hidden = true;
  });

  if (isIOS && isSafari) {
    // iOS heeft geen prompt API, toon handmatige instructie
    card.hidden = false;
    if (installBtn) installBtn.hidden = true;
    if (iosHint) iosHint.hidden = false;
  }

  if (installBtn) {
    installBtn.addEventListener('click', async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      if (choice.outcome === 'accepted') {
        card.hidden = true;
      }
      deferredPrompt = null;
    });
  }

  if (dismissBtn) {
    dismissBtn.addEventListener('click', () => {
      localStorage.setItem(STORAGE_KEY, String(Date.now()));
      card.hidden = true;
    });
  }

  window.addEventListener('appinstalled', () => {
    card.hidden = true;
    deferredPrompt = null;
  });
})();
