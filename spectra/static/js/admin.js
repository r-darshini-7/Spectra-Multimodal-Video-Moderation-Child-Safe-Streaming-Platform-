(() => {
  'use strict';

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const activeAdminId = document.querySelector('meta[name="admin-user-id"]')?.content || '';
  const apiRoot = '/admin/api';
  const state = { selectedVideo: null, activeFeedback: null, charts: {} };
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  const showToast = (message, type = 'success') => {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    byId('toastRegion')?.append(toast);
    window.setTimeout(() => toast.remove(), 5000);
  };

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    if (options.method && !['GET', 'HEAD'].includes(options.method.toUpperCase())) headers.set('X-CSRF-Token', csrfToken);
    const response = await fetch(`${apiRoot}${path}`, { ...options, headers, credentials: 'same-origin' });
    if (response.redirected && response.url.includes('/admin/login')) {
      window.location.assign(response.url);
      throw new Error('Your admin session has expired.');
    }
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
    return result;
  }

  function switchTab(name) {
    document.querySelectorAll('.admin-tab-link, .admin-mobile-bottom-nav [data-tab]').forEach((button) => {
      button.classList.toggle('active', button.dataset.tab === name);
    });
    document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.toggle('active', panel.id === `${name}-panel`));
    closeAdminProfileMenu();
    if (!window.matchMedia('(min-width: 1024px)').matches) closeAdminSidebar();
    if (name === 'analytics' && state.analytics) requestAnimationFrame(() => renderAnalytics(state.analytics));
    if (name === 'feedback') loadFeedback().catch((error) => showToast(error.message, 'error'));
  }

  function closeAdminSidebar() {
    byId('sidebarDrawer').classList.remove('open');
    byId('sidebarBackdrop').classList.remove('visible');
    byId('sidebarToggle').setAttribute('aria-expanded', 'false');
  }

  function closeAdminProfileMenu() {
    byId('adminProfileMenu').classList.add('hidden');
    byId('adminProfileToggle').setAttribute('aria-expanded', 'false');
  }

  function applyAdminSearch(query) {
    const normalized = query.trim().toLocaleLowerCase();
    const rows = [...document.querySelectorAll('.tab-panel table tbody tr')];
    if (!normalized) {
      rows.forEach((row) => { row.hidden = false; });
      return;
    }

    const matches = rows.filter((row) =>
      !row.querySelector('.empty-state') && row.textContent.toLocaleLowerCase().includes(normalized)
    );
    const matchingRows = new Set(matches);
    rows.forEach((row) => { row.hidden = !matchingRows.has(row); });
    if (matches.length) {
      const panel = matches[0].closest('.tab-panel');
      if (panel) switchTab(panel.id.replace(/-panel$/, ''));
      showToast(`${matches.length} matching record${matches.length === 1 ? '' : 's'} found.`);
    } else {
      showToast('No matching records found.', 'error');
    }
  }

  function startAdminVoiceSearch(input, button) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      showToast('Voice search is not supported by this browser.', 'error');
      return;
    }
    const recognition = new SpeechRecognition();
    recognition.lang = 'en-US';
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    button.classList.add('recording');
    recognition.onresult = (event) => {
      input.value = event.results[0][0].transcript;
      applyAdminSearch(input.value);
    };
    recognition.onend = () => button.classList.remove('recording');
    recognition.onerror = () => button.classList.remove('recording');
    recognition.start();
  }

  async function loadOverview() {
    const data = await api('/overview');
    const kpis = data.kpis || {};
    byId('kpiIngested').textContent = Number(kpis.ingested || 0).toLocaleString();
    const change = kpis.ingested_change_pct;
    const trend = change === null || change === undefined
      ? `${Number(kpis.ingested_today || 0).toLocaleString()} published today`
      : `${change > 0 ? '+' : ''}${change}% vs. yesterday`;
    byId('kpiDaily').textContent = trend;
    byId('kpiRemediated').textContent = Number(kpis.remediated || 0).toLocaleString();
    byId('kpiClean').textContent = Number(kpis.clean_bypasses || 0).toLocaleString();
    byId('kpiPending').textContent = kpis.pending_reviews == null
      ? 'Hidden'
      : Number(kpis.pending_reviews).toLocaleString();
    const pipeline = data.pipeline || {};
    const queueCount = pipeline.inference_pending;
    const reviewCount = kpis.pending_reviews;
    const publishCount = pipeline.publish_pending;
    setPipeline('inference', queueCount, queueCount == null ? 0 : Math.min(100, Number(queueCount) * 12));
    setPipeline('review', reviewCount, reviewCount == null ? 0 : Math.min(100, Number(reviewCount) * 3));
    setPipeline('publish', publishCount, publishCount == null ? 0 : Math.min(100, Number(publishCount) * 10));
    const tbody = byId('recentVideos');
    const recentHeader = tbody.closest('table')?.querySelector('thead tr');
    if (recentHeader?.lastElementChild?.textContent.trim() === 'Action') {
      recentHeader.lastElementChild.remove();
    }
    const items = data.recent_videos || [];
    tbody.innerHTML = items.length ? items.map((video) => `
      <tr><td>${escapeHtml(video.title)}</td><td>${escapeHtml(video.creator)}</td>
      <td>${escapeHtml(video.age_silo)}</td><td><span class="status-pill ${statusClass(video.status)}">${escapeHtml(video.status)}</span></td></tr>`).join('') :
      '<tr><td colspan="4" class="empty-state">No videos have been published.</td></tr>';
  }

  function setPipeline(prefix, count, percent) {
    const countNode = byId(`${prefix}Count`);
    const progress = byId(`${prefix}Progress`);
    if (countNode) {
      countNode.textContent = count == null
        ? 'Hidden in published-only view'
        : `${Number(count).toLocaleString()} ${prefix === 'inference' ? 'videos pending' : prefix === 'review' ? 'items requiring decision' : 'streams preparing'}`;
    }
    if (progress) progress.style.width = `${percent}%`;
  }

  function statusClass(status) {
    const value = String(status || '').toLowerCase();
    if (value.includes('clean') || value === 'published' || value === 'resolved' || value === 'active') return 'clean';
    if (value.includes('remediat') || value.includes('review') || value.includes('suspend')) return 'remedied';
    if (value.includes('flag') || value.includes('quarantin')) return 'flagged';
    return 'neutral';
  }

  function renderAnalytics(data) {
    if (!window.Chart) return;
    const colors = ['#7c9cff', '#ff6b6b', '#f7b267', '#b191ff', '#3dd598'];
    const common = { color: getComputedStyle(document.documentElement).getPropertyValue('--muted').trim() };
    Object.values(state.charts).forEach((chart) => chart.destroy());
    const modalityValues = ['visual', 'speech', 'acoustic', 'photosensitivity']
      .map((key) => Number(data.modalities?.[key] || 0));
    const hasViolations = modalityValues.some((value) => value > 0);
    byId('modalitiesEmpty').classList.toggle('hidden', hasViolations);
    state.charts.violations = new Chart(byId('violationsChart'), {
      type: 'doughnut',
      data: {
        labels: ['Prohibited visual objects', 'Toxic speech / profanity', 'Screamer / acoustic', 'Photosensitive strobing'],
        datasets: [{ data: modalityValues, backgroundColor: colors.slice(0, 4), borderWidth: 0 }]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: common } } }
    });
    state.charts.ingestion = new Chart(byId('ingestionChart'), {
      type: 'line',
      data: {
        labels: (data.daily || []).map((item) => item.date),
        datasets: [
          { label: 'Published', data: (data.daily || []).map((item) => item.ingested), borderColor: colors[0], backgroundColor: 'rgba(124,156,255,.12)', fill: true, tension: .35 },
          { label: 'Remediated', data: (data.daily || []).map((item) => item.remediated), borderColor: colors[4], backgroundColor: 'rgba(61,213,152,.10)', fill: true, tension: .35 }
        ]
      },
      options: { responsive: true, maintainAspectRatio: false, scales: { x: { ticks: common }, y: { ticks: common, beginAtZero: true } }, plugins: { legend: { labels: common } } }
    });
    state.charts.silos = new Chart(byId('siloChart'), {
      type: 'bar',
      data: { labels: (data.silos || []).map((item) => titleCase(item.silo)), datasets: [{ label: 'Published videos', data: (data.silos || []).map((item) => item.count), backgroundColor: colors }] },
      options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, scales: { x: { ticks: common, beginAtZero: true }, y: { ticks: common } }, plugins: { legend: { display: false } } }
    });
    byId('latencyYolo').textContent = `~${data.latency?.yolo_ms_frame ?? '—'} ms / frame`;
    byId('latencyWhisper').textContent = `~${data.latency?.whisper_seconds_chunk ?? '—'} s / chunk`;
    byId('latencyChroma').textContent = `~${data.latency?.chromadb_ms ?? '—'} ms / query`;
  }

  const titleCase = (value) => String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (char) => char.toUpperCase());
  async function loadAnalytics() {
    state.analytics = await api('/analytics');
    renderAnalytics(state.analytics);
  }

  async function loadModeration(selectedId = '') {
    const data = await api('/moderation');
    const select = byId('moderationSelect');
    const items = data.items || [];
    state.moderationItems = items;
    select.innerHTML = items.length ? items.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)} · ${escapeHtml(item.age_silo)}</option>`).join('') : '<option value="">Review queue is empty</option>';
    if (!items.length) {
      state.selectedVideo = null;
      byId('rawPlayer').removeAttribute('src');
      byId('sanitizedPlayer').removeAttribute('src');
      byId('syncScrubber').value = '0';
      byId('violationSegments').replaceChildren();
      byId('violationList').innerHTML = '<li class="empty-state">No videos require review.</li>';
      document.querySelectorAll('[data-moderation-action]').forEach((button) => { button.disabled = true; });
      return;
    }
    if (selectedId && items.some((item) => item.id === selectedId)) select.value = selectedId;
    await selectModerationItem(items.find((item) => item.id === select.value) || items[0]);
  }

  async function selectModerationItem(item) {
    state.selectedVideo = item;
    document.querySelectorAll('[data-moderation-action]').forEach((button) => { button.disabled = false; });
    const raw = byId('rawPlayer');
    const sanitized = byId('sanitizedPlayer');
    raw.pause(); sanitized.pause();
    if (item.original_url) raw.src = item.original_url;
    else raw.removeAttribute('src');
    if (item.sanitized_url) sanitized.src = item.sanitized_url;
    else sanitized.removeAttribute('src');
    const violations = item.violations || [];
    const citations = item.citations || [];
    byId('violationList').innerHTML = violations.length ? violations.map((violation, index) => {
      const sec = Number(violation.timestamp_sec || violation.start_sec || violation.timestamp || 0);
      const end = Number(violation.end_sec || (sec + 3));
      const confidence = Number(violation.confidence);
      const citation = citations[index]?.text || citations[index]?.policy || 'No matching policy snippet was stored.';
      return `<li><button type="button" class="link-button seek-violation" data-time="${sec}">${formatTime(sec)} – ${formatTime(end)}</button><strong>${escapeHtml(violation.model || violation.type || 'Model')}: ${escapeHtml(violation.label || violation.class || violation.description || 'Flagged segment')}${Number.isFinite(confidence) ? ` (${confidence.toFixed(2)})` : ''}</strong><small>Policy trigger: ${escapeHtml(citation)}</small></li>`;
    }).join('') : '<li class="empty-state">No violation detail was stored for this item.</li>';
    byId('violationList').querySelectorAll('.seek-violation').forEach((button) => button.addEventListener('click', () => {
      raw.currentTime = Number(button.dataset.time) || 0;
      sanitized.currentTime = raw.currentTime;
    }));
    renderSegments(violations, raw.duration);
    drawBoundingBoxes();
  }

  function formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '00:00';
    const minutes = Math.floor(seconds / 60);
    return `${String(minutes).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  }

  function renderSegments(violations, duration) {
    const track = byId('violationSegments');
    track.innerHTML = '';
    const length = Number(duration) || 0;
    if (!length) return;
    violations.forEach((violation) => {
      const start = Math.max(0, Number(violation.timestamp_sec || violation.start_sec || 0));
      const end = Math.max(start + 0.1, Number(violation.end_sec || start + 3));
      const marker = document.createElement('span');
      marker.className = 'segment-marker';
      marker.style.left = `${Math.min(100, start / length * 100)}%`;
      marker.style.width = `${Math.max(.4, Math.min(100 - start / length * 100, (end - start) / length * 100))}%`;
      marker.title = `${formatTime(start)} – ${formatTime(end)}`;
      track.append(marker);
    });
  }

  function drawBoundingBoxes() {
    const video = byId('rawPlayer');
    const canvas = byId('rawOverlay');
    const context = canvas.getContext('2d');
    const bounds = video.getBoundingClientRect();
    const scale = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(bounds.width * scale));
    canvas.height = Math.max(1, Math.round(bounds.height * scale));
    canvas.style.width = `${bounds.width}px`;
    canvas.style.height = `${bounds.height}px`;
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.clearRect(0, 0, bounds.width, bounds.height);
    const duration = video.duration || 0;
    const events = (state.selectedVideo?.violations || []).filter((item) =>
      item.type === 'visual' && Array.isArray(item.bbox) && item.bbox.length === 4 &&
      Math.abs(Number(item.timestamp_sec || 0) - video.currentTime) <= Math.max(.75, duration / 80)
    );
    const fit = Math.min(
      bounds.width / (video.videoWidth || bounds.width),
      bounds.height / (video.videoHeight || bounds.height)
    );
    const contentWidth = (video.videoWidth || bounds.width) * fit;
    const contentHeight = (video.videoHeight || bounds.height) * fit;
    const offsetX = (bounds.width - contentWidth) / 2;
    const offsetY = (bounds.height - contentHeight) / 2;
    events.forEach((item) => {
      const [x1, y1, x2, y2] = item.bbox.map(Number);
      if (![x1, y1, x2, y2].every(Number.isFinite)) return;
      const x = offsetX + x1 * contentWidth;
      const y = offsetY + y1 * contentHeight;
      const width = Math.max(1, (x2 - x1) * contentWidth);
      const height = Math.max(1, (y2 - y1) * contentHeight);
      context.strokeStyle = '#ff6b6b';
      context.lineWidth = 2;
      context.strokeRect(x, y, width, height);
      context.fillStyle = 'rgba(255, 107, 107, .88)';
      context.font = '12px Inter, sans-serif';
      context.fillText(`${item.class || 'Flagged object'} · ${Number(item.confidence || 0).toFixed(2)}`, x, Math.max(14, y - 5));
    });
  }

  async function actOnModeration(action) {
    if (!state.selectedVideo) return showToast('Select a review item first.', 'error');
    if (!window.confirm(`Apply "${action.replaceAll('_', ' ')}" to ${state.selectedVideo.title}?`)) return;
    try {
      await api(`/moderation/${encodeURIComponent(state.selectedVideo.id)}/action`, { method: 'POST', body: JSON.stringify({ action }) });
      showToast('Moderation decision recorded.');
      await Promise.all([loadModeration(), loadOverview()]);
    } catch (error) { showToast(error.message, 'error'); }
  }

  async function loadFeedback() {
    const data = await api('/feedback');
    const tbody = byId('feedbackRows');
    const items = data.items || [];
    const pendingFeedback = items.filter((item) => String(item.status || '').toLowerCase() !== 'resolved').length;
    const notificationBadge = byId('notifBadge');
    notificationBadge.textContent = pendingFeedback > 99 ? '99+' : String(pendingFeedback);
    notificationBadge.classList.toggle('d-none', pendingFeedback === 0);
    byId('adminNotifications').setAttribute(
      'aria-label',
      pendingFeedback ? `${pendingFeedback} unresolved feedback reports` : 'No unresolved feedback reports'
    );
    tbody.innerHTML = items.length ? items.map((item) => `<tr>
      <td>${escapeHtml(formatDate(item.created_at))}</td><td>${escapeHtml(item.reporter || 'Unknown')}</td>
      <td>${escapeHtml(titleCase(item.category))}</td><td>${escapeHtml(item.video_id || '—')}</td>
      <td>${escapeHtml(item.rating ?? '—')}</td><td><span class="status-pill ${statusClass(item.status)}">${escapeHtml(titleCase(item.status || 'open'))}</span></td>
      <td><button class="link-button open-feedback" type="button" data-id="${escapeHtml(item.feedback_id)}">Inspect</button></td></tr>`).join('') :
      '<tr><td colspan="7" class="empty-state">No feedback reports have been submitted.</td></tr>';
    tbody.querySelectorAll('.open-feedback').forEach((button) => button.addEventListener('click', () => {
      const item = items.find((entry) => entry.feedback_id === button.dataset.id);
      if (item) openFeedback(item);
    }));
  }

  function formatDate(value) {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
  }

  function openFeedback(item) {
    state.activeFeedback = item;
    byId('feedbackDetails').innerHTML = `<div><b>Report</b><p>${escapeHtml(item.description || 'No description')}</p></div><div><b>Video</b><p>${escapeHtml(item.video_id || 'Not linked')}</p></div><div><b>Age silo</b><p>${escapeHtml(item.active_age_silo || 'Not provided')}</p></div><div><b>Diagnostics</b><pre>${escapeHtml(JSON.stringify(item.diagnostics || {}, null, 2))}</pre></div>`;
    byId('feedbackStatus').value = (item.status || 'OPEN').toUpperCase();
    byId('feedbackModal').classList.remove('hidden');
  }

  async function loadPolicies() {
    const data = await api('/policies');
    const rows = byId('policyRows');
    const items = data.items || [];
    rows.innerHTML = items.length ? items.map((rule) => `<tr><td>${escapeHtml(rule.title || rule.name)}</td><td>${escapeHtml(titleCase(rule.category))}</td><td>${escapeHtml(titleCase(rule.age_silo || rule.silo))}</td><td>${escapeHtml(rule.threshold ?? '—')}</td><td><label class="switch"><input type="checkbox" class="policy-toggle" data-id="${escapeHtml(rule.rule_id)}" ${rule.active ? 'checked' : ''} aria-label="Toggle ${escapeHtml(rule.title)}"><span class="slider"></span></label></td></tr>`).join('') :
      '<tr><td colspan="5" class="empty-state">No managed policy rules have been added.</td></tr>';
    rows.querySelectorAll('.policy-toggle').forEach((toggle) => toggle.addEventListener('change', async () => {
      try {
        await api(`/policies/${encodeURIComponent(toggle.dataset.id)}`, { method: 'PATCH', body: JSON.stringify({ active: toggle.checked }) });
        showToast('Policy rule updated.');
      } catch (error) {
        toggle.checked = !toggle.checked;
        showToast(error.message, 'error');
      }
    }));
  }

  async function loadUsers() {
    const data = await api('/users');
    const rows = byId('userRows');
    const items = data.items || [];
    rows.innerHTML = items.length ? items.map((user) => `<tr><td>${escapeHtml(user.id.slice(0, 10))}…</td><td>${escapeHtml(user.email)}</td><td>${escapeHtml(user.role)}</td><td>${escapeHtml(user.children)}</td><td><span class="status-pill ${statusClass(user.status)}">${escapeHtml(titleCase(user.status))}</span></td>    <td><div class="table-actions"><button class="table-action reset-pin" type="button" data-id="${escapeHtml(user.id)}" ${user.role === 'Admin' ? 'disabled' : ''}>Send PIN reset</button><button class="table-action toggle-status" type="button" data-id="${escapeHtml(user.id)}" data-status="${user.status === 'suspended' ? 'active' : 'suspended'}" ${user.id === activeAdminId ? 'disabled' : ''}>${user.status === 'suspended' ? 'Unsuspend' : 'Suspend'}</button>${user.id === activeAdminId ? '' : `<button class="table-action danger delete-user" type="button" data-id="${escapeHtml(user.id)}">Remove</button>`}</div></td></tr>`).join('') :
      '<tr><td colspan="6" class="empty-state">No accounts found.</td></tr>';
    rows.querySelectorAll('.reset-pin').forEach((button) => button.addEventListener('click', () => resetPin(button.dataset.id)));
    rows.querySelectorAll('.toggle-status').forEach((button) => button.addEventListener('click', () => setUserStatus(button.dataset.id, button.dataset.status)));
    rows.querySelectorAll('.delete-user').forEach((button) => button.addEventListener('click', () => deleteUser(button.dataset.id)));
  }

  async function resetPin(userId) {
    try {
      await api(`/users/${encodeURIComponent(userId)}/pin-reset`, { method: 'POST', body: '{}' });
      showToast('Recovery OTP dispatched to the verified email.');
    } catch (error) { showToast(error.message, 'error'); }
  }

  async function setUserStatus(userId, status) {
    try {
      await api(`/users/${encodeURIComponent(userId)}/status`, { method: 'PATCH', body: JSON.stringify({ status }) });
      showToast(`Account ${status}.`);
      await loadUsers();
    } catch (error) { showToast(error.message, 'error'); }
  }

  async function deleteUser(userId) {
    const confirmation = 'Are you sure you want to permanently delete this user and all associated child profiles and uploads?';
    if (!window.confirm(confirmation)) return;
    try {
      await api(`/users/${encodeURIComponent(userId)}/delete`, { method: 'DELETE' });
      showToast('Account and associated records removed.');
      await loadUsers();
    } catch (error) { showToast(error.message, 'error'); }
  }

  function bindEvents() {
    document.querySelectorAll('[data-tab]').forEach((button) => {
      if (byId(`${button.dataset.tab}-panel`)) {
        button.addEventListener('click', () => switchTab(button.dataset.tab));
      }
    });
    document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => byId(button.dataset.close)?.classList.add('hidden')));
    document.querySelectorAll('[data-moderation-action]').forEach((button) => button.addEventListener('click', () => actOnModeration(button.dataset.moderationAction)));
    byId('moderationSelect').addEventListener('change', async (event) => {
      const item = state.moderationItems?.find((entry) => entry.id === event.target.value);
      if (item) await selectModerationItem(item);
    });
    byId('openRuleModal').addEventListener('click', () => byId('ruleModal').classList.remove('hidden'));
    byId('ruleForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const body = Object.fromEntries(form.entries());
      try {
        await api('/policies', { method: 'POST', body: JSON.stringify(body) });
        event.currentTarget.reset();
        byId('ruleModal').classList.add('hidden');
        showToast('Policy saved and indexed in ChromaDB.');
        await loadPolicies();
      } catch (error) { showToast(error.message, 'error'); }
    });
    byId('saveFeedbackStatus').addEventListener('click', async () => {
      if (!state.activeFeedback) return;
      try {
        await api(`/feedback/${encodeURIComponent(state.activeFeedback.feedback_id)}`, {
          method: 'PATCH', body: JSON.stringify({ status: byId('feedbackStatus').value })
        });
        byId('feedbackModal').classList.add('hidden');
        showToast('Feedback status updated.');
        await loadFeedback();
      } catch (error) { showToast(error.message, 'error'); }
    });
    byId('themeToggleBtn').addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      localStorage.setItem('spectra_theme', next);
      const icon = byId('themeIcon');
      icon.className = next === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
      byId('themeToggleBtn').setAttribute('aria-pressed', String(next === 'dark'));
      byId('themeToggleBtn').setAttribute('aria-label', `Switch to ${next === 'light' ? 'dark' : 'light'} theme`);
    });
    byId('sidebarToggle').addEventListener('click', () => {
      const desktop = window.matchMedia('(min-width: 1024px)').matches;
      if (desktop) {
        document.body.classList.toggle('sidebar-collapsed');
        byId('sidebarToggle').setAttribute(
          'aria-expanded',
          String(!document.body.classList.contains('sidebar-collapsed'))
        );
      } else {
        const open = byId('sidebarDrawer').classList.toggle('open');
        byId('sidebarBackdrop').classList.toggle('visible', open);
        byId('sidebarToggle').setAttribute('aria-expanded', String(open));
      }
    });
    byId('sidebarBackdrop').addEventListener('click', closeAdminSidebar);
    byId('adminProfileToggle').addEventListener('click', () => {
      const menu = byId('adminProfileMenu');
      const open = menu.classList.toggle('hidden') === false;
      byId('adminProfileToggle').setAttribute('aria-expanded', String(open));
    });
    document.addEventListener('click', (event) => {
      if (!event.target.closest('.admin-profile')) closeAdminProfileMenu();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeAdminProfileMenu();
        closeAdminSidebar();
      }
    });
    byId('adminSearchForm').addEventListener('submit', (event) => {
      event.preventDefault();
      applyAdminSearch(byId('searchInput').value);
    });
    byId('searchInput').addEventListener('input', (event) => {
      if (!event.currentTarget.value.trim()) applyAdminSearch('');
    });
    byId('adminMobileSearchBar').addEventListener('submit', (event) => {
      event.preventDefault();
      const query = byId('mobileSearchInput').value;
      byId('searchInput').value = query;
      applyAdminSearch(query);
      byId('adminMobileSearchBar').classList.remove('is-visible');
    });
    byId('mobileSearchInput').addEventListener('input', (event) => {
      if (!event.currentTarget.value.trim()) applyAdminSearch('');
    });
    byId('adminMobileSearchToggle').addEventListener('click', () => {
      const bar = byId('adminMobileSearchBar');
      bar.classList.toggle('is-visible');
      if (bar.classList.contains('is-visible')) byId('mobileSearchInput').focus();
    });
    byId('voiceSearchBtn').addEventListener('click', () =>
      startAdminVoiceSearch(byId('searchInput'), byId('voiceSearchBtn'))
    );
    byId('mobileVoiceSearchBtn').addEventListener('click', () =>
      startAdminVoiceSearch(byId('mobileSearchInput'), byId('mobileVoiceSearchBtn'))
    );
    const raw = byId('rawPlayer');
    const sanitized = byId('sanitizedPlayer');
    raw.addEventListener('play', () => { if (sanitized.paused) sanitized.play().catch(() => {}); });
    sanitized.addEventListener('play', () => { if (raw.paused) raw.play().catch(() => {}); });
    raw.addEventListener('pause', () => { if (!sanitized.paused) sanitized.pause(); });
    sanitized.addEventListener('pause', () => { if (!raw.paused) raw.pause(); });
    raw.addEventListener('timeupdate', () => {
      if (Math.abs(sanitized.currentTime - raw.currentTime) > .35) sanitized.currentTime = raw.currentTime;
      const length = raw.duration || 0;
      if (length) byId('syncScrubber').value = String(Math.round(raw.currentTime / length * 1000));
      drawBoundingBoxes();
    });
    raw.addEventListener('loadedmetadata', () => {
      renderSegments(state.selectedVideo?.violations || [], raw.duration);
      drawBoundingBoxes();
    });
    window.addEventListener('resize', drawBoundingBoxes);
    byId('syncScrubber').addEventListener('input', (event) => {
      const duration = raw.duration || 0;
      const target = Number(event.target.value) / 1000 * duration;
      raw.currentTime = target; sanitized.currentTime = target;
    });
    document.querySelectorAll('.modal-backdrop').forEach((modal) => modal.addEventListener('click', (event) => {
      if (event.target === modal) modal.classList.add('hidden');
    }));
  }

  async function refreshDashboard() {
    try { await loadOverview(); }
    catch (error) { showToast(error.message, 'error'); }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    bindEvents();
    document.querySelectorAll('[data-moderation-action]').forEach((button) => { button.disabled = true; });
    const savedTheme = localStorage.getItem('spectra_theme') || 'light';
    document.documentElement.dataset.theme = savedTheme;
    byId('themeIcon').className = savedTheme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
    byId('themeToggleBtn').setAttribute('aria-pressed', String(savedTheme === 'dark'));
    byId('themeToggleBtn').setAttribute('aria-label', `Switch to ${savedTheme === 'light' ? 'dark' : 'light'} theme`);
    byId('sidebarToggle').setAttribute(
      'aria-expanded',
      String(window.matchMedia('(min-width: 1024px)').matches)
    );
    try {
      await Promise.all([loadOverview(), loadAnalytics(), loadModeration(), loadFeedback(), loadPolicies(), loadUsers()]);
    } catch (error) { showToast(error.message, 'error'); }
    window.setInterval(refreshDashboard, 30000);
    window.setInterval(() => loadFeedback().catch((error) => showToast(error.message, 'error')), 30000);
  });

})();
