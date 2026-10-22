/**
 * spectra.js — Core UI: sidebar, search autocomplete, switch-PIN modal
 */

// ══ SIDEBAR DRAWER ══════════════════════════════════════════════════════════

const sidebarToggle  = document.getElementById('sidebarToggle');
const sidebarDrawer  = document.getElementById('sidebarDrawer');
const sidebarBackdrop = document.getElementById('sidebarBackdrop');
const sidebarStorageKey = 'spectra_sidebar_open';
const profileSettingsToggle = document.querySelector('[data-settings-open="true"]');

profileSettingsToggle?.addEventListener('click', (event) => {
  event.preventDefault();
  window.location.assign(profileSettingsToggle.dataset.settingsCloseUrl);
});

function openSidebar() {
  sidebarDrawer?.classList.add('open');
  sidebarBackdrop?.classList.add('visible');
  document.body.classList.add('sidebar-expanded');
  sidebarToggle?.setAttribute('aria-expanded', 'true');
  sessionStorage.setItem(sidebarStorageKey, 'true');
}

function closeSidebar() {
  sidebarDrawer?.classList.remove('open');
  sidebarBackdrop?.classList.remove('visible');
  document.body.classList.remove('sidebar-expanded');
  sidebarToggle?.setAttribute('aria-expanded', 'false');
  sessionStorage.setItem(sidebarStorageKey, 'false');
}

if (sessionStorage.getItem(sidebarStorageKey) === 'true') {
  openSidebar();
}

sidebarToggle?.addEventListener('click', () => {
  const sidebarOpen = sidebarDrawer?.classList.contains('open');
  sidebarOpen ? closeSidebar() : openSidebar();
});

sidebarDrawer?.querySelectorAll('a.sidebar-item').forEach((link) => {
  link.addEventListener('click', closeSidebar);
});

// ══ MOBILE SEARCH TOGGLE ════════════════════════════════════════════════════

const mobileSearchToggle = document.getElementById('mobileSearchToggle');
const mobileSearchBar = document.getElementById('mobileSearchBar');
const mobileSearchInput = document.getElementById('mobileSearchInput');
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

function normalizeSearchQuery(value) {
  return value?.trim().replace(/\s+/g, ' ') || '';
}

function executeSearch(value) {
  const query = normalizeSearchQuery(value);
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
      executeSearch(query);
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

document.getElementById('searchForm')?.addEventListener('submit', (event) => {
  event.preventDefault();
  hideSuggestions();
  executeSearch(searchInput?.value);
});
searchInput?.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') hideSuggestions();
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
document.getElementById('mobileSearchForm')?.addEventListener('submit', (event) => {
  event.preventDefault();
  hideSearchSuggestions(mobileSearchSuggestions);
  executeSearch(mobileSearchInput?.value);
});
mobileSearchInput?.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') hideSearchSuggestions(mobileSearchSuggestions);
});

async function fetchSuggestions(q, container) {
  const normalizedQuery = normalizeSearchQuery(q);
  const requestId = ++suggestionRequestId;
  if (!container) return;
  container.replaceChildren();
  const loading = document.createElement('div');
  loading.className = 'search-suggestions-message';
  loading.textContent = 'Finding videos...';
  container.appendChild(loading);
  container.classList.add('show');

  try {
    const res = await fetch(`/api/search-suggest?q=${encodeURIComponent(normalizedQuery)}`);
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
  if (!e.target.closest('.search-wrapper') && !e.target.closest('.video-search-suggestion')) hideSuggestions();
  if (!e.target.closest('#mobileSearchBar') && !e.target.closest('#mobileSearchToggle') && !e.target.closest('.video-search-suggestion')) {
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

const notificationBell = document.getElementById('notificationBell');
const notificationDropdown = document.getElementById('notificationDropdown');
const notificationFeed = document.getElementById('notificationFeed');
const notificationUnreadCount = document.getElementById('notificationUnreadCount');
const notificationMarkAllButton = document.getElementById('markAllNotificationsRead');
const notificationIcons = {
  badge: 'fa-award',
  sparkles: 'fa-wand-magic-sparkles',
  video: 'fa-video',
  subscriber_upload: 'fa-video',
  creator_upload: 'fa-video',
  trending: 'fa-arrow-trend-up',
  download: 'fa-download',
  download_started: 'fa-download',
  download_completed: 'fa-download',
  clock: 'fa-clock',
  watch_limit: 'fa-clock',
  bell: 'fa-bell',
};
let notificationRequest = null;
let notificationItems = [];
let notificationUnreadTotal = 0;

function updateNotificationCount(count, enabled = true) {
  const badge = document.getElementById('notifBadge');
  if (!badge) return;
  const notificationsLink = badge.closest('.notifications-link');
  if (notificationsLink) notificationsLink.hidden = !enabled;
  const unread = Math.max(0, Number(count) || 0);
  notificationUnreadTotal = unread;
  if (enabled && unread > 0) {
    badge.textContent = unread > 99 ? '99+' : String(unread);
    badge.classList.remove('d-none');
    badge.setAttribute('aria-label', `${unread} unread notifications`);
  } else {
    badge.textContent = '';
    badge.classList.add('d-none');
    badge.removeAttribute('aria-label');
  }
  if (notificationUnreadCount) {
    notificationUnreadCount.textContent = unread > 99 ? '99+' : String(unread);
    notificationUnreadCount.classList.toggle('d-none', unread === 0);
  }
  if (notificationMarkAllButton) {
    notificationMarkAllButton.hidden = unread === 0;
  }
}

function relativeNotificationTime(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.valueOf())) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - date.valueOf()) / 1000));
  if (seconds < 60) return 'Just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date);
}

function renderNotifications(items) {
  if (!notificationFeed) return;
  notificationFeed.replaceChildren();
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'notification-empty';
    const illustration = document.createElement('span');
    illustration.className = 'notification-empty-icon';
    illustration.innerHTML = '<i class="fas fa-bell-slash" aria-hidden="true"></i>';
    const text = document.createElement('strong');
    text.textContent = 'No new notifications';
    const hint = document.createElement('span');
    hint.textContent = 'You’re all caught up.';
    empty.append(illustration, text, hint);
    notificationFeed.appendChild(empty);
    return;
  }

  items.forEach((item) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `notification-card${item.is_read ? '' : ' is-unread'}`;
    button.setAttribute('role', 'listitem');
    button.dataset.notificationId = item.id;

    const icon = document.createElement('span');
    icon.className = `notification-card-icon notification-icon-${item.icon_type}`;
    const iconElement = document.createElement('i');
    iconElement.className = `fas ${notificationIcons[item.icon_type] || notificationIcons[item.type] || 'fa-bell'}`;
    iconElement.setAttribute('aria-hidden', 'true');
    icon.appendChild(iconElement);

    const content = document.createElement('span');
    content.className = 'notification-card-content';
    const title = document.createElement('strong');
    title.className = 'notification-card-title';
    title.textContent = item.title;
    const message = document.createElement('span');
    message.className = 'notification-card-message';
    message.textContent = item.message;
    const timestamp = document.createElement('time');
    timestamp.className = 'notification-card-time';
    timestamp.dateTime = item.created_at;
    timestamp.textContent = relativeNotificationTime(item.created_at);
    content.append(title, message, timestamp);

    if (!item.is_read) {
      const unreadDot = document.createElement('span');
      unreadDot.className = 'notification-unread-dot';
      unreadDot.setAttribute('aria-label', 'Unread');
      button.append(icon, content, unreadDot);
    } else {
      button.append(icon, content);
    }

    button.addEventListener('click', () => openNotification(item));
    notificationFeed.appendChild(button);
  });
}

async function fetchNotifCount() {
  if (notificationRequest) return notificationRequest;
  const notificationsUrl = notificationDropdown?.dataset.notificationsUrl;
  if (!notificationsUrl) return;
  notificationRequest = (async () => {
  try {
    const response = await fetch(notificationsUrl, {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!response.ok) throw new Error(`Notification request failed (${response.status}).`);
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'Unable to load notifications.');
    notificationItems = data.notifications || [];
    updateNotificationCount(data.unread_count, data.enabled !== false);
    renderNotifications(notificationItems);
  } catch (error) {
    console.error('Unable to load notifications:', error);
    if (notificationFeed) {
      notificationFeed.replaceChildren();
      const errorMessage = document.createElement('div');
      errorMessage.className = 'notification-loading';
      errorMessage.textContent = 'Notifications could not be loaded. Please try again.';
      notificationFeed.appendChild(errorMessage);
    }
  } finally {
    notificationRequest = null;
  }
  })();
  return notificationRequest;
}

async function markNotificationRead(notificationId = null) {
  const markReadUrl = notificationDropdown?.dataset.markReadUrl;
  if (!markReadUrl) throw new Error('Notification read endpoint is unavailable.');
  const response = await fetch(markReadUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(notificationId ? { notification_id: notificationId } : {}),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || `Unable to update notifications (${response.status}).`);
  }
}

async function openNotification(item) {
  const destination = item.target_url;
  if (!destination || !destination.startsWith('/') || destination.startsWith('//')) {
    console.error('Blocked invalid notification destination.');
    return;
  }
  try {
    if (!item.is_read) await markNotificationRead(item.id);
    window.location.assign(destination);
  } catch (error) {
    console.error('Unable to mark notification as read:', error);
  }
}

notificationBell?.addEventListener('click', async () => {
  const opening = notificationDropdown?.classList.contains('d-none');
  notificationDropdown?.classList.toggle('d-none', !opening);
  notificationBell.setAttribute('aria-expanded', String(Boolean(opening)));
  if (opening) {
    await fetchNotifCount();
    notificationFeed?.querySelector('.notification-card')?.focus();
  }
});

notificationDropdown?.addEventListener('click', (event) => event.stopPropagation());
document.addEventListener('click', (event) => {
  if (!notificationDropdown?.contains(event.target) && !notificationBell?.contains(event.target)) {
    notificationDropdown?.classList.add('d-none');
    notificationBell?.setAttribute('aria-expanded', 'false');
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !notificationDropdown?.classList.contains('d-none')) {
    notificationDropdown.classList.add('d-none');
    notificationBell?.setAttribute('aria-expanded', 'false');
    notificationBell?.focus();
  }
});

notificationMarkAllButton?.addEventListener('click', async () => {
  const previousItems = notificationItems;
  const previousUnreadCount = notificationUnreadTotal;
  notificationItems = notificationItems.map((item) => ({ ...item, is_read: true }));
  updateNotificationCount(0);
  renderNotifications(notificationItems);
  try {
    await markNotificationRead();
  } catch (error) {
    console.error('Unable to mark notifications as read:', error);
    notificationItems = previousItems;
    updateNotificationCount(previousUnreadCount);
    renderNotifications(notificationItems);
    await fetchNotifCount();
  }
});

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

document.addEventListener('spectra:notification-preference-changed', fetchNotifCount);
