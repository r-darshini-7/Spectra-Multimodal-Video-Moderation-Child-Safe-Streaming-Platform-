/**
 * player.js — Custom Video.js / HLS.js player controls for the watch page
 */

let player = null;
let currentSpeed = 1.0;
let isLocked = false;
let ccEnabled = false;
let watchTimeInterval = null;
let watchTimeLastTick = null;
let watchProgressInterval = null;
let resumePositionApplied = false;
let autoplayAttempted = false;
let mp4FallbackAttempted = false;
let watchLimitMinutes = null;
let watchLimitExtensionSeconds = 0;
let watchLimitModal = null;
let watchLimitDayKey = null;

// ══ INITIALISE PLAYER ════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  const vjsEl = document.getElementById('spectaPlayer');
  if (!vjsEl) return;

  const commentsToggle = document.getElementById('commentsToggle');
  const commentsContent = document.getElementById('commentsContent');
  commentsToggle?.addEventListener('click', () => {
    const expanded = commentsToggle.getAttribute('aria-expanded') === 'true';
    commentsToggle.setAttribute('aria-expanded', String(!expanded));
    commentsContent.hidden = expanded;
  });

  // Init Video.js
  player = videojs('spectaPlayer', {
    controls: false,  // We use custom controls
    preload: 'auto',
    fluid: true,
    responsive: true,
    autoplay: false,
    playbackRates: [0.5, 1.0, 1.5, 2.0],
    html5: {
      vhs: { overrideNative: true },
      nativeAudioTracks: false,
      nativeVideoTracks: false,
    },
  });
  const limitModalElement = document.getElementById('watchLimitModal');
  if (limitModalElement && window.bootstrap) {
    watchLimitModal = window.bootstrap.Modal.getOrCreateInstance(limitModalElement, {
      backdrop: 'static',
      keyboard: false,
    });
  }
  document.getElementById('extendWatchLimit')?.addEventListener('click', () => {
    watchLimitExtensionSeconds += 5 * 60;
    if (watchLimitDayKey) {
      localStorage.setItem(
        `spectra_watch_limit_extension:${VIEWER_PROFILE_ID}:${watchLimitDayKey}`,
        String(watchLimitExtensionSeconds),
      );
    }
    watchLimitModal?.hide();
    player?.play();
  });
  document.getElementById('closeWatchLimit')?.addEventListener('click', () => {
    window.location.assign('/feed');
  });
  document.getElementById('ignoreWatchLimit')?.addEventListener('click', () => {
    const dayKey = watchLimitDayKey || new Date().toISOString().slice(0, 10);
    localStorage.setItem(`spectra_watch_limit_ignored:${VIEWER_PROFILE_ID}:${dayKey}`, '1');
    watchLimitModal?.hide();
    player?.play();
  });

  player.on('error', () => {
    const error = player.error();
    const source = player.currentSource();
    if (mp4FallbackAttempted || error?.code !== 4 || !MP4_SRC || !source?.src?.includes('/hls/')) return;

    mp4FallbackAttempted = true;
    const resumeAt = player.currentTime() || 0;
    player.one('loadedmetadata', () => {
      if (resumeAt > 0 && resumeAt < player.duration()) player.currentTime(resumeAt);
    });
    player.src({src: MP4_SRC, type: 'video/mp4'});
  });

  // Player events → update custom controls
  player.on('play', () => {
    document.getElementById('playIcon')?.classList.replace('fa-play', 'fa-pause');
  });
  player.on('playing', startWatchTimeTracking);
  player.on('pause', () => {
    document.getElementById('playIcon')?.classList.replace('fa-pause', 'fa-play');
    stopWatchTimeTracking();
  });
  player.on('waiting', stopWatchTimeTracking);
  player.on('timeupdate', onTimeUpdate);
  player.on('loadedmetadata', () => {
    const dur = player.duration() || 0;
    const range = document.getElementById('progressRange');
    if (range) {
      range.max = dur;
      updateProgressFill(range);
    }
    if (!resumePositionApplied) {
      resumePositionApplied = true;
      const resumeAt = Number(VIDEO_RESUME_TIME);
      if (Number.isFinite(resumeAt) && resumeAt > 3 && resumeAt < dur - 3) {
        player.currentTime(resumeAt);
      }
    }
    updateTimeDisplay();
    syncCaptionButton();
    if (!autoplayAttempted) {
      autoplayAttempted = true;
      attemptAutoplay();
    }
  });
  player.on('ended', () => {
    document.getElementById('playIcon')?.classList.replace('fa-pause', 'fa-play');
    stopWatchTimeTracking();
    savePlaybackPosition(player.duration());
  });

  // Progress range input
  document.getElementById('progressRange')?.addEventListener('input', (e) => {
    updateProgressFill(e.target);
    if (player) player.currentTime(parseFloat(e.target.value));
  });

  // Record watch history on start
  player.on('play', () => {
    if (VIDEO_ID && VIDEO_TRACKING_ENABLED) {
      savePlaybackPosition(undefined, true);
    }
  });

  // Share modal — update start-at time
  document.getElementById('shareModal')?.addEventListener('show.bs.modal', () => {
    const t = player ? Math.floor(player.currentTime()) : 0;
    const m = Math.floor(t / 60), s = t % 60;
    const el = document.getElementById('startAtTime');
    if (el) el.textContent = `${m}:${s.toString().padStart(2,'0')}`;
    updateShareUrl();
  });
  document.getElementById('startAtCheck')?.addEventListener('change', updateShareUrl);
  window.addEventListener('pagehide', stopWatchTimeTracking);
  document.addEventListener('fullscreenchange', updateFullscreenButton);
  document.addEventListener('webkitfullscreenchange', updateFullscreenButton);
});

function startWatchTimeTracking() {
  if (!VIDEO_ID) return;
  if (VIDEO_WATCH_TIME_ENABLED && !watchTimeInterval) {
    watchTimeLastTick = Date.now();
    watchTimeInterval = window.setInterval(recordWatchTime, 15000);
  }
  if (VIDEO_TRACKING_ENABLED && !watchProgressInterval) {
    watchProgressInterval = window.setInterval(savePlaybackPosition, 10000);
  }
}

function stopWatchTimeTracking(savePosition = true) {
  if (watchTimeInterval) {
    window.clearInterval(watchTimeInterval);
    watchTimeInterval = null;
    recordWatchTime();
  }
  if (watchProgressInterval) {
    window.clearInterval(watchProgressInterval);
    watchProgressInterval = null;
  }
  if (savePosition) savePlaybackPosition();
}

function savePlaybackPosition(positionSeconds = player?.currentTime(), playStarted = false) {
  if (!VIDEO_ID || !VIDEO_TRACKING_ENABLED || !Number.isFinite(positionSeconds)) return;
  const durationSeconds = player?.duration();
  fetch('/api/history/record', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      video_id: VIDEO_ID,
      position_seconds: Math.max(0, Math.round(positionSeconds * 10) / 10),
      ...(Number.isFinite(durationSeconds) && durationSeconds > 0
        ? {duration_seconds: Math.round(durationSeconds * 10) / 10}
        : {}),
      ...(playStarted ? {play_started: true} : {}),
    }),
    keepalive: true,
  }).catch(() => {});
}

function attemptAutoplay() {
  if (!player) return;
  const playback = player.play();
  playback?.catch(() => {
    if (!player) return;
    player.muted(true);
    player.play()?.catch(() => {});
  });
}

function recordWatchTime() {
  if (!watchTimeLastTick) return;
  const now = Date.now();
  const seconds = Math.min(30, Math.floor((now - watchTimeLastTick) / 1000));
  watchTimeLastTick = now;
  if (seconds < 1) return;

  fetch(`/api/watch-time/${encodeURIComponent(VIDEO_ID)}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({seconds}),
    keepalive: true,
  }).then(async (response) => {
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || 'Could not update watch time.');
    watchLimitMinutes = result.limit_minutes;
    if (!Number.isFinite(watchLimitMinutes) || watchLimitMinutes <= 0) return;
    watchLimitDayKey = result.day_key;
    watchLimitExtensionSeconds = Number(
      localStorage.getItem(`spectra_watch_limit_extension:${VIEWER_PROFILE_ID}:${watchLimitDayKey}`) || 0,
    );
    const ignoredKey = `spectra_watch_limit_ignored:${VIEWER_PROFILE_ID}:${result.day_key}`;
    if (
      result.watched_seconds >= watchLimitMinutes * 60 + watchLimitExtensionSeconds
      && localStorage.getItem(ignoredKey) !== '1'
    ) {
      player?.pause();
      watchLimitModal?.show();
    }
  }).catch((error) => {
    console.error('Unable to check the daily watch limit:', error);
  });
}

// ══ TRANSPORT CONTROLS ═══════════════════════════════════════════════════════

function togglePlayPause() {
  if (!player || isLocked) return;
  player.paused() ? player.play() : player.pause();
}

function seekRelative(delta) {
  if (!player || isLocked) return;
  player.currentTime(Math.max(0, player.currentTime() + delta));
}

async function toggleFullscreen() {
  const shell = document.getElementById('playerShell');
  const fullscreenContent = document.getElementById('playerFullscreenContent');
  if (!shell) return;

  const isFullscreen = document.fullscreenElement === shell || document.webkitFullscreenElement === shell;
  if (isFullscreen) {
    if (document.exitFullscreen) await document.exitFullscreen().catch(() => {});
    else document.webkitExitFullscreen?.();
    return;
  }

  try {
    if (shell.requestFullscreen) await shell.requestFullscreen();
    else if (shell.webkitRequestFullscreen) shell.webkitRequestFullscreen();
    else if (player) await player.requestFullscreen();
    else return;

    if (window.matchMedia('(max-width: 767.98px)').matches) {
      try {
        if (typeof screen.orientation?.lock !== 'function') throw new Error('Orientation lock unavailable');
        await screen.orientation.lock('landscape');
      } catch {
        if (window.matchMedia('(orientation: portrait)').matches) {
          fullscreenContent?.classList.add('fullscreen-rotate');
        }
      }
    }
  } catch {
    // Fullscreen and orientation lock are unavailable in some mobile browsers.
  }
}

function updateFullscreenButton() {
  const shell = document.getElementById('playerShell');
  const fullscreen = document.fullscreenElement === shell
    || document.webkitFullscreenElement === shell;
  const icon = document.querySelector('#playerShell .transport-btn[title="Fullscreen"] i');
  if (icon) icon.className = fullscreen ? 'fas fa-compress' : 'fas fa-expand';
  if (!fullscreen) {
    screen.orientation?.unlock?.();
    document.getElementById('playerFullscreenContent')?.classList.remove('fullscreen-rotate');
  }
}

// ══ PROGRESS & TIME ══════════════════════════════════════════════════════════

function onTimeUpdate() {
  if (!player) return;
  const cur = player.currentTime() || 0;
  const dur = player.duration() || 0;
  const range = document.getElementById('progressRange');
  if (range) {
    range.value = cur;
    range.max = dur;
    updateProgressFill(range);
  }
  updateTimeDisplay(cur, dur);
}

function updateProgressFill(range) {
  const max = Number(range.max);
  const value = Number(range.value);
  const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  range.style.setProperty('--progress', `${percent}%`);
}

function updateTimeDisplay(cur, dur) {
  cur = cur ?? (player?.currentTime() ?? 0);
  dur = dur ?? (player?.duration() ?? 0);
  const el = document.getElementById('timeDisplay');
  if (el) el.textContent = `${fmtTime(cur)} / ${fmtTime(dur)}`;
}

function fmtTime(sec) {
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  if (h > 0) return `${h}:${pad(m)}:${pad(ss)}`;
  return `${m}:${pad(ss)}`;
}
function pad(n) { return n.toString().padStart(2,'0'); }

// ══ QUALITY SELECTOR ═════════════════════════════════════════════════════════

function setQuality(btn) {
  document.querySelectorAll('.quality-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  // With HLS.js the ABR handles quality; for manual: could force level
  // For this integration we simply show the selection
}

// ══ SPEED CONTROL ════════════════════════════════════════════════════════════

function adjustSpeed(delta) {
  currentSpeed = Math.min(2.0, Math.max(0.5, currentSpeed + delta));
  // Round to 0.5 steps
  currentSpeed = Math.round(currentSpeed / 0.5) * 0.5;
  if (player) player.playbackRate(currentSpeed);
  const display = document.getElementById('speedDisplay');
  if (display) display.textContent = `${currentSpeed.toFixed(1)}×`;
}

// ══ LOCK SCREEN ══════════════════════════════════════════════════════════════

function toggleLock() {
  isLocked = !isLocked;
  const overlay = document.getElementById('lockOverlay');
  const icon = document.getElementById('lockBtnIcon');
  const text = document.getElementById('lockBtnText');

  if (overlay) overlay.classList.toggle('d-none', !isLocked);
  if (icon) icon.className = isLocked ? 'fas fa-lock-open me-1' : 'fas fa-lock me-1';
  if (text) text.textContent = isLocked ? 'Unlock' : 'Lock';
}

// ══ SETTINGS DRAWER ══════════════════════════════════════════════════════════

function toggleSettings() {
  const drawer = document.getElementById('playerSettingsDrawer');
  drawer?.classList.toggle('show');
}

// ══ CAPTIONS ═════════════════════════════════════════════════════════════════

function toggleCC() {
  if (!player) return;
  const tracks = getCaptionTracks();
  if (!tracks.length) return;
  const showing = tracks.some((track) => track.mode === 'showing');
  ccEnabled = !showing;
  tracks.forEach((track) => { track.mode = ccEnabled ? 'showing' : 'disabled'; });
  syncCaptionButton();
}

function getCaptionTracks() {
  const tracks = player?.textTracks();
  if (!tracks) return [];
  return Array.from({length: tracks.length}, (_, index) => tracks[index])
    .filter((track) => track.kind === 'captions' || track.kind === 'subtitles');
}

function syncCaptionButton() {
  const tracks = getCaptionTracks();
  ccEnabled = tracks.some((track) => track.mode === 'showing');
  const btn = document.getElementById('ccBtn');
  btn?.classList.toggle('active', ccEnabled);
  if (btn) btn.disabled = tracks.length === 0;
}

// ══ SOCIAL INTERACTIONS ══════════════════════════════════════════════════════

async function toggleLike(videoId) {
  try {
    const res = await fetch(`/api/like/${videoId}`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('likeCount').textContent = data.count;
      document.getElementById('likeBtn')?.classList.toggle('active', data.liked);
      if (data.liked) document.getElementById('dislikeBtn')?.classList.remove('active');
    }
  } catch {}
}

async function toggleDislike(videoId) {
  try {
    const res = await fetch(`/api/dislike/${videoId}`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('dislikeCount').textContent = data.count;
      document.getElementById('dislikeBtn')?.classList.toggle('active', data.disliked);
      if (data.disliked) document.getElementById('likeBtn')?.classList.remove('active');
    }
  } catch {}
}

async function toggleSubscribe(channelId) {
  try {
    const res = await fetch(`/api/subscribe/${channelId}`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      const btn = document.getElementById('subscribeBtn');
      const text = document.getElementById('subBtnText');
      const count = document.getElementById('subCount');
      if (btn) btn.classList.toggle('subscribed', data.subscribed);
      if (text) text.textContent = data.subscribed ? 'Subscribed' : 'Subscribe';
      if (count) count.textContent = data.count.toLocaleString();
    }
  } catch {}
}

// ══ COMMENTS ═════════════════════════════════════════════════════════════════

async function postComment(videoId) {
  const input = document.getElementById('newCommentInput');
  const text = input?.value?.trim();
  if (!text) return;

  try {
    const res = await fetch('/api/comment', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({video_id: videoId, text}),
    });
    const data = await res.json();
    if (data.ok) {
      input.value = '';
      prependComment(data.comment);
    }
  } catch {}
}

function prependComment(c) {
  const list = document.getElementById('commentsList');
  if (!list) return;
  const div = document.createElement('div');
  div.className = 'comment-item';
  div.id = `cmt_${c._id}`;
  div.innerHTML = `
    <div class="comment-avatar">${escHtml((c.persona_name||'U')[0].toUpperCase())}</div>
    <div class="flex-grow-1">
      <div class="comment-header">
        <span class="comment-author fw-semibold">${escHtml(c.persona_name)}</span>
        <span class="comment-time text-muted small ms-2">${c.created_at_rel}</span>
      </div>
      <p class="comment-text mb-1">${escHtml(c.text)}</p>
      <div class="d-flex gap-3 align-items-center">
        <button class="btn btn-sm comment-action" onclick="likeComment('${c._id}',this)">
          <i class="fas fa-thumbs-up"></i> <span>0</span>
        </button>
        <button class="btn btn-sm comment-action" onclick="toggleReply('${c._id}')">Reply</button>
      </div>
    </div>`;
  list.prepend(div);
}

function cancelComment() {
  const input = document.getElementById('newCommentInput');
  if (input) input.value = '';
}

async function likeComment(commentId, btn) {
  try {
    const res = await fetch(`/api/comment/like/${commentId}`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      const span = btn.querySelector('span');
      if (span) span.textContent = data.likes;
    }
  } catch {}
}

function toggleReply(commentId) {
  const area = document.getElementById(`replyArea_${commentId}`);
  area?.classList.toggle('d-none');
  document.getElementById(`replyInput_${commentId}`)?.focus();
}

async function postReply(videoId, parentId) {
  const input = document.getElementById(`replyInput_${parentId}`);
  const text = input?.value?.trim();
  if (!text) return;
  try {
    const res = await fetch('/api/comment', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({video_id: videoId, text, parent_id: parentId}),
    });
    const data = await res.json();
    if (data.ok) {
      input.value = '';
      let container = document.getElementById(`replies_${parentId}`);
      if (!container) {
        container = document.createElement('div');
        container.className = 'replies-container mt-2';
        container.id = `replies_${parentId}`;
        document.getElementById(`cmt_${parentId}`)?.querySelector('.flex-grow-1')?.appendChild(container);
      }
      const r = data.comment;
      const div = document.createElement('div');
      div.className = 'reply-item d-flex gap-2 mt-2';
      div.innerHTML = `
        <div class="reply-avatar">${escHtml((r.persona_name||'U')[0].toUpperCase())}</div>
        <div>
          <span class="fw-semibold small">${escHtml(r.persona_name)}</span>
          <span class="text-muted small ms-2">${r.created_at_rel}</span>
          <p class="mb-0 small">${escHtml(r.text)}</p>
        </div>`;
      container.appendChild(div);
      document.getElementById(`replyArea_${parentId}`)?.classList.add('d-none');
    }
  } catch {}
}

// ══ SHARE MODAL ═══════════════════════════════════════════════════════════════

function updateShareUrl() {
  const checked = document.getElementById('startAtCheck')?.checked;
  const base = window.location.origin + window.location.pathname + window.location.search;
  const t = player ? Math.floor(player.currentTime()) : 0;
  const url = checked ? `${base}&t=${t}` : base;
  const el = document.getElementById('shareUrl');
  if (el) el.value = url;
}

function copyShareUrl() {
  const url = document.getElementById('shareUrl')?.value;
  if (!url) return;
  navigator.clipboard.writeText(url).then(() => {
    const btn = document.querySelector('#shareModal .btn-spectra');
    if (btn) { btn.innerHTML = '<i class="fas fa-check"></i>'; setTimeout(() => { btn.innerHTML = '<i class="fas fa-copy"></i>'; }, 1500); }
  });
}

// ══ SAVE MODALS ═══════════════════════════════════════════════════════════════

async function addWatchLater(videoId) {
  await fetch('/api/watch-later/add', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({video_id: videoId}),
  });
  bootstrap.Modal.getInstance(document.getElementById('saveModal'))?.hide();
}

async function saveToPlaylist(playlistId, videoId) {
  await fetch('/api/playlist/add', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({playlist_id: playlistId, video_id: videoId}),
  });
  bootstrap.Modal.getInstance(document.getElementById('saveModal'))?.hide();
}

async function createAndSave(videoId) {
  const name = document.getElementById('newPlaylistName')?.value?.trim();
  if (!name) return;
  const res = await fetch('/api/playlist/create', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name}),
  });
  const data = await res.json();
  if (data.ok) {
    await saveToPlaylist(data.playlist_id, videoId);
  }
}

// ══ UTILS ════════════════════════════════════════════════════════════════════

function escHtml(s) {
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Handle t= timestamp seek from URL
(function handleUrlTimestamp() {
  const params = new URLSearchParams(window.location.search);
  const t = params.get('t');
  if (t && !isNaN(parseInt(t))) {
    document.addEventListener('DOMContentLoaded', () => {
      setTimeout(() => { if (player) player.currentTime(parseInt(t)); }, 800);
    });
  }
})();

// Timestamp links in comments → seek player
document.addEventListener('click', (e) => {
  const link = e.target.closest('.timestamp-link');
  if (link && player) {
    e.preventDefault();
    const sec = parseInt(link.dataset.sec || '0', 10);
    player.currentTime(sec);
    player.play();
    window.scrollTo({top: 0, behavior: 'smooth'});
  }
});
