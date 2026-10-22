/**
 * spectra.js — Core UI: sidebar, search autocomplete, switch-PIN modal
 */

// ══ SIDEBAR DRAWER ══════════════════════════════════════════════════════════

const sidebarToggle  = document.getElementById('sidebarToggle');
const sidebarDrawer  = document.getElementById('sidebarDrawer');
const sidebarBackdrop = document.getElementById('sidebarBackdrop');
const profileSettingsToggle = document.querySelector('[data-settings-open="true"]');

profileSettingsToggle?.addEventListener('click', (event) => {
  event.preventDefault();
  const previousPage = document.referrer ? new URL(document.referrer) : null;
  if (previousPage?.origin === window.location.origin && previousPage.pathname !== window.location.pathname) {
    window.history.back();
    return;
  }
  window.location.assign(profileSettingsToggle.dataset.settingsCloseUrl);
});

function openSidebar() {
  sidebarDrawer?.classList.add('open');
  sidebarBackdrop?.classList.add('visible');
  document.body.classList.add('sidebar-expanded');
  sidebarToggle?.setAttribute('aria-expanded', 'true');
}

function closeSidebar() {
  sidebarDrawer?.classList.remove('open');
  sidebarBackdrop?.classList.remove('visible');
  document.body.classList.remove('sidebar-expanded');
  sidebarToggle?.setAttribute('aria-expanded', 'false');
}

sidebarToggle?.addEventListener('click', () => {
  const sidebarOpen = sidebarDrawer?.classList.contains('open');
  sidebarOpen ? closeSidebar() : openSidebar();
});

// ══ MOBILE SEARCH TOGGLE ════════════════════════════════════════════════════

const mobileSearchToggle = document.getElementById('mobileSearchToggle');
const mobileSearchBar = document.getElementById('mobileSearchBar');
const mobileSearchInput = document.getElementById('mobileSearchInput');
const mobileSearchBtn = document.getElementById('mobileSearchBtn');
const mobileSearchSuggestions = document.getElementById('mobileSearchSuggestions');
const searchPersonaId = document.querySelector('.spectra-navbar')?.dataset.personaId || 'anonymous';
const recentSearchStorageKey = `spectra_recent_searches:${searchPersonaId}`;

mobileSearchToggle?.addEventListener('click', () => {
  const isOpening = mobileSearchBar?.classList.contains('d-none');
  mobileSearchBar?.classList.toggle('d-none');
  mobileSearchToggle.setAttribute('aria-expanded', String(Boolean(isOpening)));
  mobileSearchToggle.setAttribute(
    'aria-label',
    isOpening ? 'Close search' : 'Open search'
  );
  if (isOpening) {
    showRecentSearches(mobileSearchSuggestions, mobileSearchInput);
    mobileSearchInput?.focus();
  } else {
    hideSearchSuggestions(mobileSearchSuggestions);
  }
});

// ══ SEARCH AUTOCOMPLETE ══════════════════════════════════════════════════════

const searchInput = document.getElementById('searchInput');
const searchSuggestions = document.getElementById('searchSuggestions');
const searchBtn = document.getElementById('searchBtn');
const voiceSearchBtn = document.getElementById('voiceSearchBtn');
let suggestTimer = null;
let suggestionRequestId = 0;

function getRecentSearches() {
  try {
    const searches = JSON.parse(localStorage.getItem(recentSearchStorageKey) || '[]');
    return Array.isArray(searches)
      ? searches.filter((query) => typeof query === 'string' && query.trim()).slice(0, 10)
      : [];
  } catch {
    return [];
  }
}

function saveRecentSearch(query) {
  const normalizedQuery = query.trim();
  if (!normalizedQuery) return;
  const searches = getRecentSearches().filter(
    (item) => item.toLocaleLowerCase() !== normalizedQuery.toLocaleLowerCase()
  );
  try {
    localStorage.setItem(
      recentSearchStorageKey,
      JSON.stringify([normalizedQuery, ...searches].slice(0, 10))
    );
  } catch (error) {
    console.error('Unable to save recent search:', error);
  }
}

function submitSearch(value) {
  const query = value?.trim();
  if (!query) return;
  saveRecentSearch(query);
  window.location.href = `/search?q=${encodeURIComponent(query)}`;
}

function showRecentSearches(container, input) {
  if (!container) return;
  suggestionRequestId += 1;
  const searches = getRecentSearches();
  if (!searches.length) {
    hideSearchSuggestions(container);
    return;
  }

  container.replaceChildren();
  const heading = document.createElement('div');
  heading.className = 'search-suggestions-heading';
  heading.textContent = 'Recent searches';
  container.appendChild(heading);

  searches.forEach((query) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'search-suggest-item recent-search-item';
    item.textContent = query;
    item.addEventListener('click', () => {
      if (input) input.value = query;
      submitSearch(query);
    });
    container.appendChild(item);
  });
  container.classList.add('show');
}

function startVoiceSearch(inputId, button) {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const input = document.getElementById(inputId);
  if (!SpeechRecognition || !input || !button) return;

  const recognition = new SpeechRecognition();
  recognition.lang = 'en-US';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  button.classList.add('recording');
  recognition.onresult = (event) => {
    input.value = event.results[0][0].transcript;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  };
  recognition.onend = () => button.classList.remove('recording');
  recognition.onerror = () => button.classList.remove('recording');
  recognition.start();
}

voiceSearchBtn?.addEventListener('click', () => startVoiceSearch('searchInput', voiceSearchBtn));
document.getElementById('mobileVoiceSearchBtn')?.addEventListener('click', (event) => {
  startVoiceSearch('mobileSearchInput', event.currentTarget);
});

searchInput?.addEventListener('input', () => {
  clearTimeout(suggestTimer);
  const q = searchInput.value.trim();
  if (!q) {
    showRecentSearches(searchSuggestions, searchInput);
    return;
  }
  suggestTimer = setTimeout(() => fetchSuggestions(q, searchSuggestions), 220);
});

searchInput?.addEventListener('focus', () => {
  if (!searchInput.value.trim()) {
    showRecentSearches(searchSuggestions, searchInput);
  }
});

searchInput?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    hideSuggestions();
    submitSearch(searchInput.value);
  }
  if (e.key === 'Escape') hideSuggestions();
});

searchBtn?.addEventListener('click', () => {
  const q = searchInput?.value?.trim();
  if (q) submitSearch(q);
  else showRecentSearches(searchSuggestions, searchInput);
});

mobileSearchInput?.addEventListener('focus', () => {
  if (!mobileSearchInput.value.trim()) {
    showRecentSearches(mobileSearchSuggestions, mobileSearchInput);
  }
});
mobileSearchInput?.addEventListener('input', () => {
  clearTimeout(suggestTimer);
  if (!mobileSearchInput.value.trim()) {
    showRecentSearches(mobileSearchSuggestions, mobileSearchInput);
  } else {
    suggestTimer = setTimeout(
      () => fetchSuggestions(mobileSearchInput.value.trim(), mobileSearchSuggestions),
      220
    );
  }
});
mobileSearchInput?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') submitSearch(mobileSearchInput.value);
  if (event.key === 'Escape') hideSearchSuggestions(mobileSearchSuggestions);
});
mobileSearchBtn?.addEventListener('click', () => {
  const query = mobileSearchInput?.value?.trim();
  if (query) submitSearch(query);
  else showRecentSearches(mobileSearchSuggestions, mobileSearchInput);
});

async function fetchSuggestions(q, container) {
  const requestId = ++suggestionRequestId;
  if (!container) return;
  container.replaceChildren();
  const loading = document.createElement('div');
  loading.className = 'search-suggestions-message';
  loading.textContent = 'Finding videos...';
  container.appendChild(loading);
  container.classList.add('show');

  try {
    const res = await fetch(`/api/search-suggest?q=${encodeURIComponent(q)}`);
    if (!res.ok) throw new Error(`Suggestion request failed (${res.status}).`);
    const items = await res.json();
    if (requestId !== suggestionRequestId) return;
    container.replaceChildren();
    if (!Array.isArray(items) || !items.length) {
      const empty = document.createElement('div');
      empty.className = 'search-suggestions-message';
      empty.textContent = 'No matching videos found.';
      container.appendChild(empty);
      container.classList.add('show');
      return;
    }

    items.forEach((item) => {
      const link = document.createElement('a');
      link.className = 'search-suggest-item video-search-suggestion';
      link.href = `/watch?v=${encodeURIComponent(item.id)}`;
      if (item.thumbnail) {
        const thumbnail = document.createElement('img');
        thumbnail.className = 'search-suggestion-thumbnail';
        thumbnail.src = `/media/${encodeURIComponent(item.id)}/thumbnails/${encodeURIComponent(item.thumbnail)}`;
        thumbnail.alt = '';
        thumbnail.loading = 'lazy';
        thumbnail.addEventListener('error', () => thumbnail.remove(), { once: true });
        link.appendChild(thumbnail);
      }

      if (item.creator_avatar) {
        const avatar = document.createElement('img');
        avatar.className = 'search-suggestion-avatar';
        avatar.src = item.creator_avatar;
        avatar.alt = '';
        avatar.loading = 'lazy';
        avatar.addEventListener('error', () => avatar.remove(), { once: true });
        link.appendChild(avatar);
      }

      const details = document.createElement('span');
      details.className = 'search-suggestion-details';
      const title = document.createElement('span');
      title.className = 'search-suggestion-title';
      title.textContent = item.title || 'Untitled video';
      details.appendChild(title);
      const metadata = [item.creator_name, item.category].filter(Boolean).join(' · ');
      if (metadata) {
        const subtitle = document.createElement('span');
        subtitle.className = 'search-suggestion-meta';
        subtitle.textContent = metadata;
        details.appendChild(subtitle);
      }
      link.appendChild(details);
      container.appendChild(link);
    });
    container.classList.add('show');
  } catch (error) {
    if (requestId !== suggestionRequestId) return;
    console.error('Unable to fetch video suggestions:', error);
    container.replaceChildren();
    const failure = document.createElement('div');
    failure.className = 'search-suggestions-message';
    failure.textContent = 'Video suggestions are unavailable. Please try again.';
    container.appendChild(failure);
    container.classList.add('show');
  }
}

function hideSuggestions() {
  hideSearchSuggestions(searchSuggestions);
}

function hideSearchSuggestions(container) {
  if (!container) return;
  suggestionRequestId += 1;
  container.classList.remove('show');
  container.replaceChildren();
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('.search-wrapper')) hideSuggestions();
  if (!e.target.closest('#mobileSearchBar') && !e.target.closest('#mobileSearchToggle')) {
    hideSearchSuggestions(mobileSearchSuggestions);
  }
});

// ══ QUICK-SWITCH PIN MODAL ═══════════════════════════════════════════════════

let switchPinBuffer = '';

function initSwitchKeypad() {
  // Digit buttons
  document.querySelectorAll('[data-switch-digit]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (switchPinBuffer.length >= 5) return;
      switchPinBuffer += btn.dataset.switchDigit;
      updateSwitchDots();
      if (switchPinBuffer.length === 5) submitSwitchPin();
    });
  });

  document.getElementById('swClear')?.addEventListener('click', () => {
    switchPinBuffer = '';
    updateSwitchDots();
    clearSwitchError();
  });

  document.getElementById('swBack')?.addEventListener('click', () => {
    switchPinBuffer = switchPinBuffer.slice(0, -1);
    updateSwitchDots();
    clearSwitchError();
  });

  // Reset on modal open
  document.getElementById('switchModal')?.addEventListener('show.bs.modal', () => {
    switchPinBuffer = '';
    updateSwitchDots();
    clearSwitchError();
  });
}

function updateSwitchDots() {
  for (let i = 0; i < 5; i++) {
    const dot = document.getElementById(`sd${i}`);
    if (dot) dot.classList.toggle('filled', i < switchPinBuffer.length);
  }
}

function clearSwitchError() {
  const err = document.getElementById('switchError');
  if (err) { err.classList.add('d-none'); err.textContent = ''; }
}

async function submitSwitchPin() {
  try {
    const res = await fetch('/auth/switch-pin', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({pin: switchPinBuffer}),
    });
    const data = await res.json();
    if (data.ok) {
      window.location.href = data.redirect;
    } else {
      const err = document.getElementById('switchError');
      if (err) { err.textContent = data.error || 'Invalid PIN.'; err.classList.remove('d-none'); }
      switchPinBuffer = '';
      updateSwitchDots();
    }
  } catch {
    switchPinBuffer = '';
    updateSwitchDots();
  }
}

// ══ USER THEME TOGGLE ════════════════════════════════════════════════════════

function toggleUserTheme() {
  const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  setUserThemePreference(next);
}

function applyUserThemePreference(preference) {
  const root = document.documentElement;
  root.dataset.themePreference = preference;
  const resolved = preference === 'system'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : preference;
  root.setAttribute('data-theme', resolved);
  localStorage.setItem('spectra_theme', preference);
  _applyThemeIcon(resolved);
}

async function setUserThemePreference(preference) {
  if (!['light', 'dark', 'system'].includes(preference)) return;
  applyUserThemePreference(preference);
  if (!document.body.classList.contains('user-layout')) return;
  try {
    const response = await fetch('/auth/profile/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({theme: preference}),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || 'Could not save theme.');
  } catch (error) {
    console.error('Unable to save theme preference:', error);
  }
}

function _applyThemeIcon(theme) {
  const icon = document.getElementById('themeIcon');
  if (!icon) return;
  icon.className = theme === 'light' ? 'fas fa-moon' : 'fas fa-sun';
  const toggle = document.getElementById('themeToggleBtn');
  toggle?.setAttribute('aria-pressed', String(theme === 'dark'));
  toggle?.setAttribute('aria-label', `Switch to ${theme === 'light' ? 'dark' : 'light'} theme`);
}

// Restore saved theme on load
(function restoreTheme() {
  const isLoginPage = document.body.classList.contains('login-page');
  const saved = isLoginPage
    ? 'light'
    : (document.documentElement.dataset.themePreference || localStorage.getItem('spectra_theme') || 'system');
  const resolved = saved === 'system'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : saved;
  document.documentElement.dataset.themePreference = saved;
  document.documentElement.setAttribute('data-theme', resolved);
  document.addEventListener('DOMContentLoaded', () => _applyThemeIcon(resolved));
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (document.documentElement.dataset.themePreference === 'system') {
      applyUserThemePreference('system');
    }
  });
})();

// ══ UTILS ════════════════════════════════════════════════════════════════════

function escHtml(str) {
  return (str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

const creatorActionStatusTimers = new WeakMap();

function setCreatorActionStatus(status, message) {
  if (!status) return;
  status.textContent = message;
  window.clearTimeout(creatorActionStatusTimers.get(status));
  if (message) {
    creatorActionStatusTimers.set(status, window.setTimeout(() => {
      status.textContent = '';
      creatorActionStatusTimers.delete(status);
    }, 4000));
  }
}

async function toggleCreatorSubscription(creatorId, buttonId, statusId) {
  const button = document.getElementById(buttonId);
  const status = document.getElementById(statusId);
  if (!button) return;

  button.disabled = true;
  try {
    const response = await fetch(`/api/subscribe-creator/${encodeURIComponent(creatorId)}`, {
      method: 'POST',
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'Subscription failed.');

    const creatorName = button.dataset.creatorName || 'Creator';
    button.classList.toggle('following', data.subscribed);
    button.setAttribute('aria-pressed', String(data.subscribed));
    button.setAttribute(
      'aria-label',
      `${data.subscribed ? 'Unsubscribe from' : 'Subscribe to'} ${creatorName}`
    );
    button.title = data.subscribed ? 'Remove from Subscriptions' : 'Add to Subscriptions';
    setCreatorActionStatus(
      status,
      data.subscribed ? 'Creator added to Subscriptions.' : 'Creator removed from Subscriptions.',
    );
  } catch (error) {
    setCreatorActionStatus(status, error.message);
  } finally {
    button.disabled = false;
  }
}

async function shareCreatorProfile(profileUrl, creatorName, statusId) {
  const status = document.getElementById(statusId);
  const url = new URL(profileUrl, window.location.origin).href;

  if (navigator.share) {
    try {
      await navigator.share({ title: `${creatorName} on Spectra.`, url });
      setCreatorActionStatus(status, 'Profile shared.');
      return;
    } catch (error) {
      if (error.name === 'AbortError') return;
    }
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
      setCreatorActionStatus(status, 'Profile link copied.');
      return;
    }
  } catch {}

  const field = document.createElement('textarea');
  field.value = url;
  field.setAttribute('readonly', '');
  field.style.position = 'fixed';
  field.style.opacity = '0';
  document.body.appendChild(field);
  field.select();
  const copied = document.execCommand('copy');
  field.remove();
  setCreatorActionStatus(status, copied ? 'Profile link copied.' : `Copy this link: ${url}`);
}

// ══ DYNAMIC NOTIFICATION BADGE ═══════════════════════════════════════════════

async function fetchNotifCount() {
  try {
    const res = await fetch('/api/notification-count');
    if (!res.ok) return;
    const data = await res.json();
    const badge = document.getElementById('notifBadge');
    if (!badge) return;
    if (data.count > 0) {
      badge.textContent = data.count > 99 ? '99+' : data.count;
      badge.classList.remove('d-none');
    } else {
      badge.classList.add('d-none');
    }
  } catch {}
}

// ══ IDLE SESSION TIMEOUT ═════════════════════════════════════════════════════

(function initIdleTimeout() {
  // Only on authenticated pages (notifBadge present = logged-in layout)
  const IDLE_MS = 30 * 60 * 1000; // 30 minutes
  let idleTimer;

  function resetTimer() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(checkSession, IDLE_MS);
  }

  async function checkSession() {
    try {
      const res = await fetch('/api/session-check');
      const data = await res.json();
      if (!data.logged_in) window.location.href = '/auth/login';
    } catch {}
  }

  if (document.getElementById('notifBadge')) {
    ['mousemove','keydown','click','scroll','touchstart'].forEach(ev =>
      document.addEventListener(ev, resetTimer, { passive: true })
    );
    resetTimer();
  }
})();

// ══ INIT ═════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  initSwitchKeypad();
  // Fetch live notification count on page load
  if (document.getElementById('notifBadge')) {
    fetchNotifCount();
    // Refresh every 60 seconds
    setInterval(fetchNotifCount, 60_000);
  }
});
