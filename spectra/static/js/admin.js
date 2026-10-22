(() => {
  'use strict';

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const activeAdminId = document.querySelector('meta[name="admin-user-id"]')?.content || '';
  const apiRoot = '/admin/api';
  const sidebarStorageKey = 'spectra_sidebar_open';
  const state = {
    activeFeedback: null,
    charts: {},
    platformCharts: {},
    metricsUnavailable: false,
    analyticsUnavailable: false,
    analyticsRequest: null,
    platformAnalyticsRequest: null,
    activityView: 'daily',
    notificationItems: [],
    notificationFilter: 'all',
    notificationUnavailable: false,
  };
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

  async function api(path, options = {}, root = apiRoot) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    if (options.method && !['GET', 'HEAD'].includes(options.method.toUpperCase())) headers.set('X-CSRF-Token', csrfToken);
    const response = await fetch(`${root}${path}`, { ...options, headers, credentials: 'same-origin' });
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
    closeAdminSidebar();
    if (name === 'analytics') loadAnalytics().catch(handleAnalyticsError);
    if (name === 'feedback') loadFeedback().catch((error) => showToast(error.message, 'error'));
  }

  function openAdminSidebar() {
    byId('sidebarDrawer').classList.add('open');
    byId('sidebarBackdrop').classList.add('visible');
    document.body.classList.add('sidebar-expanded');
    byId('sidebarToggle').setAttribute('aria-expanded', 'true');
    sessionStorage.setItem(sidebarStorageKey, 'true');
  }

  function closeAdminSidebar() {
    byId('sidebarDrawer').classList.remove('open');
    byId('sidebarBackdrop').classList.remove('visible');
    document.body.classList.remove('sidebar-expanded');
    byId('sidebarToggle').setAttribute('aria-expanded', 'false');
    sessionStorage.setItem(sidebarStorageKey, 'false');
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

  function updateMetric(id, value) {
    const node = byId(id);
    const formattedValue = Number(value).toLocaleString();
    if (node.textContent === formattedValue) return;
    node.textContent = formattedValue;
    node.animate?.(
      [
        { opacity: 0.45, transform: 'translateY(4px)' },
        { opacity: 1, transform: 'translateY(0)' },
      ],
      { duration: 320, easing: 'ease-out' }
    );
  }

  async function loadMetrics() {
    const metrics = await api('/metrics', {}, '/api/admin');
    updateMetric('kpiActiveUsers', metrics.active_users);
    updateMetric('kpiActiveCreators', metrics.uploading_creators);
    updateMetric('kpiPublishedVideos', metrics.published_videos);
    updateMetric('kpiRemediated', metrics.auto_remediated);
  }

  async function refreshMetrics() {
    try {
      await loadMetrics();
      state.metricsUnavailable = false;
    } catch (error) {
      if (!state.metricsUnavailable) showToast(error.message, 'error');
      state.metricsUnavailable = true;
    }
  }

  function statusClass(status) {
    const value = String(status || '').toLowerCase();
    if (value.includes('clean') || value === 'published' || value === 'resolved' || value === 'active') return 'clean';
    if (value.includes('remediat') || value.includes('review') || value.includes('suspend')) return 'remedied';
    if (value.includes('flag') || value.includes('quarantin')) return 'flagged';
    return 'neutral';
  }

  function formatWatchTime(seconds) {
    const totalHours = Math.floor(Math.max(0, Number(seconds) || 0) / 3600);
    const minutes = Math.floor((Math.max(0, Number(seconds) || 0) % 3600) / 60);
    if (totalHours) return `${totalHours.toLocaleString()} hr${totalHours === 1 ? '' : 's'}`;
    return `${minutes} min`;
  }

  function updateAnalyticsText(id, value) {
    const node = byId(id);
    if (node.textContent === String(value)) return;
    node.textContent = String(value);
    node.animate?.(
      [{ opacity: 0.45, transform: 'translateY(4px)' }, { opacity: 1, transform: 'translateY(0)' }],
      { duration: 280, easing: 'ease-out' }
    );
  }

  function analyticsChartTextColor() {
    return getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#718096';
  }

  function toggleChartEmpty(chart, emptyId, hasData) {
    byId(chart).classList.toggle('hidden', !hasData);
    byId(emptyId).classList.toggle('hidden', hasData);
  }

  function renderAnalytics(data) {
    if (!window.Chart) return;
    const colors = ['#6952c8', '#f2a53b', '#19a985', '#dc5d70', '#3286d4'];
    const tickColor = analyticsChartTextColor();
    const common = { color: tickColor };
    Object.values(state.charts).forEach((chart) => chart.destroy());
    state.charts = {};

    const kpis = data.kpis || {};
    updateAnalyticsText('analyticsWatchTime', formatWatchTime(kpis.watch_time_seconds));
    const watchChange = kpis.watch_time_change_pct;
    updateAnalyticsText(
      'analyticsWatchChange',
      watchChange === null || watchChange === undefined
        ? Number(kpis.watch_time_previous_seconds || 0) === 0
          ? 'No watch time in the previous period'
          : 'No previous-period comparison available'
        : `${watchChange > 0 ? '+' : ''}${watchChange}% vs. previous period`
    );
    updateAnalyticsText('analyticsIngested', `${Number(kpis.ingested || 0).toLocaleString()} videos`);
    updateAnalyticsText('analyticsRemediationRate', `${Number(kpis.auto_remediation_rate || 0).toFixed(1)}%`);
    updateAnalyticsText(
      'analyticsRemediationDetail',
      `${Number(kpis.auto_remediated || 0).toLocaleString()} of ${Number(kpis.ingested || 0).toLocaleString()} ingested videos remediated`
    );
    updateAnalyticsText(
      'analyticsSafetyScore',
      kpis.safety_score === null || kpis.safety_score === undefined
        ? 'Not available'
        : `${Number(kpis.safety_score).toFixed(1)}%`
    );
    updateAnalyticsText(
      'analyticsSafetyDetail',
      `${Number(kpis.reported_videos || 0)} distinct videos reported in this period · proxy against the live catalog, not model accuracy`
    );

    const violations = data.violations || {};
    const violationRows = [
      Number(violations.weapons || 0),
      Number(violations.other_visual || 0),
      Number(violations.transcript_toxicity || 0),
      Number(violations.policy_review || 0),
    ];
    const hasViolations = violationRows.some((value) => value > 0);
    toggleChartEmpty('adminViolationsChart', 'adminViolationsEmpty', hasViolations);
    state.charts.violations = new Chart(byId('adminViolationsChart'), {
      type: 'bar',
      data: {
        labels: ['YOLOv11 · Visual', 'Whisper → BERT · Transcript', 'Policy review escalation'],
        datasets: [
          { label: 'Weapons', data: [violationRows[0], 0, 0], backgroundColor: colors[3] },
          { label: 'Other visual findings', data: [violationRows[1], 0, 0], backgroundColor: colors[0] },
          { label: 'Transcript toxicity', data: [0, violationRows[2], 0], backgroundColor: colors[1] },
          { label: 'Policy review escalations', data: [0, 0, violationRows[3]], backgroundColor: colors[4] },
        ]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { stacked: true, beginAtZero: true, ticks: { ...common, precision: 0 } },
          y: { stacked: true, ticks: common },
        },
        plugins: { legend: { position: 'bottom', labels: { ...common, usePointStyle: true, boxWidth: 8 } } },
      },
    });

    const actions = data.actions || {};
    const actionValues = ['clean_pass', 'visual_blur', 'audio_bleep', 'manual_review', 'rejected']
      .map((key) => Number(actions[key] || 0));
    toggleChartEmpty('adminActionsChart', 'adminActionsEmpty', actionValues.some((value) => value > 0));
    state.charts.actions = new Chart(byId('adminActionsChart'), {
      type: 'doughnut',
      data: {
        labels: ['Clean pass', 'Visual frame blur', 'Audio muted / bleeped', 'Automated check follow-up', 'Rejected / blocked'],
        datasets: [{ data: actionValues, backgroundColor: colors, borderColor: '#fff', borderWidth: 2 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '62%',
        plugins: { legend: { position: 'bottom', labels: { ...common, usePointStyle: true, boxWidth: 8 } } },
      },
    });

    const silos = data.silos || [];
    const hasSiloActivity = silos.some((item) => Number(item.watch_seconds) > 0);
    toggleChartEmpty('adminSilosChart', 'adminSilosEmpty', hasSiloActivity);
    state.charts.silos = new Chart(byId('adminSilosChart'), {
      type: 'bar',
      data: {
        labels: silos.map((item) => item.label),
        datasets: [{
          label: 'Watch time (hours)',
          data: silos.map((item) => Number(item.watch_seconds || 0) / 3600),
          backgroundColor: colors,
          borderRadius: 6,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { beginAtZero: true, ticks: common, title: { display: true, text: 'Hours watched', color: tickColor } },
          y: { ticks: common },
        },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: (context) => `${formatWatchTime(Number(context.raw) * 3600)} watched`,
        } } },
      },
    });

    renderActivityChart(data.activity || {}, state.activityView);
    renderCreatorRows(data.creators || []);
  }

  const titleCase = (value) => String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (char) => char.toUpperCase());
  async function loadAnalytics() {
    state.analyticsRequest?.abort();
    state.analyticsRequest = new AbortController();
    const range = byId('analyticsRange').value;
    state.analytics = await api(
      `/analytics?range=${encodeURIComponent(range)}`,
      { signal: state.analyticsRequest.signal, cache: 'no-store' },
      '/api/admin'
    );
    if (byId('analyticsRange').value !== range) return;
    renderAnalytics(state.analytics);
    await loadPlatformViewingAnalytics();
    state.analyticsUnavailable = false;
  }

  function setPlatformChartEmpty(chartId, emptyId, hasData) {
    byId(chartId)?.classList.toggle('hidden', !hasData);
    byId(emptyId)?.classList.toggle('hidden', hasData);
  }

  function clearPlatformCharts() {
    Object.values(state.platformCharts).forEach((chart) => chart?.destroy());
    state.platformCharts = {};
  }

  function formatPlatformDuration(seconds) {
    const total = Math.max(0, Number(seconds) || 0);
    if (total >= 3600) return `${(total / 3600).toLocaleString(undefined, { maximumFractionDigits: 1 })} hrs`;
    return `${Math.round(total / 60).toLocaleString()} mins`;
  }

  function renderPlatformViewingAnalytics(data) {
    clearPlatformCharts();
    const kpis = data.kpis || {};
    updateAnalyticsText('globalWatchTime', formatPlatformDuration(kpis.watch_seconds));
    updateAnalyticsText('globalDailyAverage', `${Number(kpis.average_daily_minutes || 0).toLocaleString()} mins/day`);
    updateAnalyticsText('globalCompletionRate', `${Number(kpis.completion_rate || 0).toFixed(1)}%`);
    const silo = kpis.most_active_silo;
    updateAnalyticsText(
      'globalActiveSilo',
      silo ? `${silo.label} (${Number(silo.percentage || 0)}%)` : 'Not available'
    );
    updateAnalyticsText('globalDiversityIndex', `${Number(data.exploration_diversity || 0).toFixed(1)} categories`);

    const textColor = '#64748b';
    const gridColor = '#eef0f5';
    const colors = ['#5c45fd', '#22c55e', '#06b6d4', '#f59e0b', '#ef5da8', '#8b5cf6', '#14b8a6', '#f97316'];
    const timeline = data.timeline || [];
    setPlatformChartEmpty('globalTimelineChart', 'globalTimelineEmpty', timeline.some((item) => item.hours > 0));
    if (timeline.some((item) => item.hours > 0)) {
      state.platformCharts.timeline = new Chart(byId('globalTimelineChart'), {
        type: 'bar',
        data: {
          labels: timeline.map((item) => item.period),
          datasets: [{ data: timeline.map((item) => item.hours), backgroundColor: '#7057f5', borderRadius: 6, maxBarThickness: 38 }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: {
            x: { ticks: { color: textColor, maxRotation: 45, minRotation: 0 }, grid: { display: false } },
            y: { beginAtZero: true, ticks: { color: textColor, callback: (value) => `${value}h` }, grid: { color: gridColor } },
          },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (context) => `${context.raw} hours` } } },
        },
      });
    }

    const weekday = data.weekday_weekend || {};
    const weekdayValues = [Number(weekday.weekdays_minutes || 0), Number(weekday.weekend_minutes || 0)];
    setPlatformChartEmpty('globalWeekdayChart', 'globalWeekdayEmpty', weekdayValues.some((value) => value > 0));
    if (weekdayValues.some((value) => value > 0)) {
      state.platformCharts.weekday = new Chart(byId('globalWeekdayChart'), {
        type: 'bar',
        data: { labels: ['Weekdays (Mon–Fri)', 'Weekend (Sat–Sun)'], datasets: [{ data: weekdayValues, backgroundColor: ['#5c45fd', '#22c55e'], borderRadius: 7 }] },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: {
            x: { ticks: { color: textColor }, grid: { display: false } },
            y: { beginAtZero: true, ticks: { color: textColor, callback: (value) => `${value}m` }, grid: { color: gridColor } },
          },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (context) => `${Number(context.raw).toLocaleString()} minutes` } } },
        },
      });
    }

    const dayparts = data.dayparts || [];
    setPlatformChartEmpty('globalDaypartChart', 'globalDaypartEmpty', dayparts.some((item) => item.seconds > 0));
    if (dayparts.some((item) => item.seconds > 0)) {
      state.platformCharts.dayparts = new Chart(byId('globalDaypartChart'), {
        type: 'doughnut',
        data: { labels: dayparts.map((item) => item.label), datasets: [{ data: dayparts.map((item) => item.seconds), backgroundColor: colors.slice(0, 4), borderColor: '#fff', borderWidth: 3 }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: '58%', plugins: { legend: { position: 'bottom', labels: { color: textColor, usePointStyle: true, boxWidth: 8 } } } },
      });
    }

    const categories = data.categories || [];
    setPlatformChartEmpty('globalCategoryChart', 'globalCategoryEmpty', categories.some((item) => item.seconds > 0));
    if (categories.some((item) => item.seconds > 0)) {
      state.platformCharts.categories = new Chart(byId('globalCategoryChart'), {
        type: 'doughnut',
        data: { labels: categories.map((item) => item.category), datasets: [{ data: categories.map((item) => item.seconds), backgroundColor: colors, borderColor: '#fff', borderWidth: 3 }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: '58%', plugins: { legend: { position: 'bottom', labels: { color: textColor, usePointStyle: true, boxWidth: 8 } } } },
      });
    }

    const interactions = data.interactions || {};
    const interactionValues = [Number(interactions.likes || 0), Number(interactions.saves || 0), Number(interactions.downloads || 0)];
    setPlatformChartEmpty('globalInteractionChart', 'globalInteractionEmpty', interactionValues.some((value) => value > 0));
    if (interactionValues.some((value) => value > 0)) {
      state.platformCharts.interactions = new Chart(byId('globalInteractionChart'), {
        type: 'bar',
        data: { labels: ['Likes', 'Saves', 'Downloads'], datasets: [{ data: interactionValues, backgroundColor: colors.slice(0, 3), borderRadius: 6 }] },
        options: {
          indexAxis: 'y', responsive: true, maintainAspectRatio: false,
          scales: {
            x: { beginAtZero: true, ticks: { color: textColor, precision: 0 }, grid: { color: gridColor } },
            y: { ticks: { color: textColor }, grid: { display: false } },
          },
          plugins: { legend: { display: false } },
        },
      });
    }

    const videos = data.top_videos || [];
    byId('globalTopVideos').innerHTML = videos.length ? videos.map((video) => `
      <tr><td title="${escapeHtml(video.title)}">${escapeHtml(video.title)}</td>
      <td>${escapeHtml(video.creator)}</td><td>${Number(video.plays || 0).toLocaleString()}</td>
      <td>${Number(video.replays || 0).toLocaleString()}</td>
      <td>${video.average_watch_pct === null ? '—' : `${Number(video.average_watch_pct).toFixed(1)}%`}</td>
      <td>${escapeHtml(video.silo_category)}</td></tr>`).join('') :
      '<tr><td class="platform-table-empty" colspan="6">No replay data recorded for this period.</td></tr>';

    const creators = data.top_creators || [];
    byId('globalTopCreators').innerHTML = creators.length ? creators.map((creator, index) => `
      <tr><td>${index + 1}</td><td>${escapeHtml(creator.name)}</td>
      <td>${formatPlatformDuration(creator.watch_seconds)}</td></tr>`).join('') :
      '<tr><td class="platform-table-empty" colspan="3">No creator watch-time data for this period.</td></tr>';
  }

  async function loadPlatformViewingAnalytics() {
    state.platformAnalyticsRequest?.abort();
    state.platformAnalyticsRequest = new AbortController();
    const range = byId('analyticsRange').value;
    const silo = byId('viewingSilo').value;
    const error = byId('platformViewingError');
    error.classList.add('hidden');
    const params = new URLSearchParams({ range, silo });
    try {
      const data = await api(
        `/analytics/viewing?${params.toString()}`,
        { signal: state.platformAnalyticsRequest.signal, cache: 'no-store' },
        '/api/admin'
      );
      if (byId('analyticsRange').value !== range || byId('viewingSilo').value !== silo) return;
      renderPlatformViewingAnalytics(data);
    } catch (requestError) {
      if (requestError.name === 'AbortError') return;
      error.textContent = requestError.message || 'Platform viewing analytics could not be loaded.';
      error.classList.remove('hidden');
      throw requestError;
    }
  }

  function handleAnalyticsError(error) {
    if (error.name === 'AbortError') return;
    if (!state.analyticsUnavailable) showToast(error.message, 'error');
    state.analyticsUnavailable = true;
  }

  function renderActivityChart(activity, view) {
    state.charts.activity?.destroy();
    const records = activity[view] || [];
    const hasActivity = records.some((item) =>
      Number(item.watch_seconds || 0) > 0 || Number(item.uploads || 0) > 0
    );
    toggleChartEmpty('adminActivityChart', 'adminActivityEmpty', hasActivity);
    byId('activityChartCaption').textContent = view === 'daily'
      ? `Daily platform activity (UTC) over the most recent ${activity.daily_trend_days || records.length} days`
      : activity.range === 'all'
        ? 'Hourly totals (UTC) across the most recent 30 days'
        : 'Hourly totals (UTC) across the selected period';
    const chartLabels = records.map((item) => view === 'daily' ? item.date : item.hour);
    const watchHours = records.map((item) => Number(item.watch_seconds || 0) / 3600);
    const uploads = records.map((item) => Number(item.uploads || 0));
    const tickColor = analyticsChartTextColor();
    state.charts.activity = new Chart(byId('adminActivityChart'), {
      type: 'line',
      data: {
        labels: chartLabels,
        datasets: [
          {
            label: 'Watch time (hours)',
            data: watchHours,
            yAxisID: 'y',
            borderColor: '#6952c8',
            backgroundColor: 'rgba(105,82,200,.13)',
            fill: true,
            tension: .32,
          },
          {
            label: 'Video uploads',
            data: uploads,
            yAxisID: 'y1',
            borderColor: '#19a985',
            backgroundColor: 'rgba(25,169,133,.08)',
            fill: false,
            tension: .32,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { ticks: { color: tickColor, maxTicksLimit: 8 }, grid: { display: false } },
          y: { beginAtZero: true, position: 'left', ticks: { color: tickColor }, title: { display: true, text: 'Watch hours', color: tickColor } },
          y1: { beginAtZero: true, position: 'right', ticks: { color: tickColor, precision: 0 }, title: { display: true, text: 'Uploads', color: tickColor }, grid: { drawOnChartArea: false } },
        },
        plugins: { legend: { labels: { color: tickColor, usePointStyle: true, boxWidth: 8 } } },
      },
    });
    const hourly = activity.hourly || [];
    const peak = hourly.reduce((best, item) =>
      Number(item.watch_seconds || 0) > Number(best?.watch_seconds || 0) ? item : best, null);
    byId('adminPeakHour').textContent = peak && Number(peak.watch_seconds) > 0
      ? `Peak viewing hour: ${peak.hour} · ${formatWatchTime(peak.watch_seconds)}`
      : 'Peak viewing hour will appear when watch activity is recorded.';
    document.querySelectorAll('[data-activity-view]').forEach((button) => {
      const selected = button.dataset.activityView === view;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
  }

  function renderCreatorRows(creators) {
    const tbody = byId('adminCreatorRows');
    if (!creators.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No creator uploads found for this period.</td></tr>';
      return;
    }
    tbody.innerHTML = creators.map((creator) => {
      const badgeClass = creator.trust_badge === 'High Trust'
        ? 'high-trust'
        : creator.trust_badge === 'Needs Review'
          ? 'needs-review'
          : 'restricted';
      return `<tr>
        <td>${escapeHtml(creator.name)}</td>
        <td>${Number(creator.uploads || 0).toLocaleString()}</td>
        <td>${creator.published === 0 ? '—' : `${Number(creator.clean_rate || 0).toFixed(1)}%`}</td>
        <td>${Number(creator.remediated || 0).toLocaleString()}</td>
        <td>${Number(creator.flagged_violations || 0).toLocaleString()}</td>
        <td><span class="creator-trust-badge ${badgeClass}">${escapeHtml(creator.trust_badge)}</span></td>
      </tr>`;
    }).join('');
  }

  async function loadFeedback() {
    const data = await api('/feedback');
    const tbody = byId('feedbackRows');
    const items = data.items || [];
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
    const requestedReport = new URLSearchParams(window.location.search).get('report_id');
    if (requestedReport) {
      const report = items.find((item) => item.feedback_id === requestedReport);
      if (report) {
        window.history.replaceState(null, '', `${window.location.pathname}#feedback`);
        switchTab('feedback');
        openFeedback(report);
      } else {
        window.history.replaceState(null, '', `${window.location.pathname}#feedback`);
        switchTab('feedback');
        showToast('The linked user report could not be found in the current report list.', 'error');
      }
    }
  }

  function notificationCategoryLabel(category) {
    return ({
      moderation: 'Safety violation',
      creator: 'Creator safety',
      pipeline: 'Pipeline',
      report: 'User report',
    })[category] || 'Admin alert';
  }

  function notificationActionLabel(category) {
    return ({
      moderation: 'Review Video',
      creator: 'Review Creator',
      pipeline: 'Inspect Pipeline',
      report: 'Review Report',
    })[category] || 'Open alert';
  }

  function relativeNotificationTime(value) {
    const timestamp = typeof value === 'string' && !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
      ? `${value}Z`
      : value;
    const createdAt = new Date(timestamp);
    if (Number.isNaN(createdAt.getTime())) return 'Time unavailable';
    const seconds = Math.max(0, Math.floor((Date.now() - createdAt.getTime()) / 1000));
    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
    return createdAt.toLocaleDateString();
  }

  function notificationTargetUrl(targetUrl) {
    if (typeof targetUrl !== 'string' || !targetUrl.startsWith('/') || targetUrl.startsWith('//')) {
      return '/admin/';
    }
    const parsed = new URL(targetUrl, window.location.origin);
    return parsed.origin === window.location.origin
      ? `${parsed.pathname}${parsed.search}${parsed.hash}`
      : '/admin/';
  }

  function updateNotificationUnreadCount(count) {
    const unreadCount = Math.max(0, Number(count) || 0);
    const badge = byId('notifBadge');
    const trigger = byId('adminNotifications');
    badge.textContent = unreadCount > 99 ? '99+' : String(unreadCount);
    badge.classList.toggle('d-none', unreadCount === 0);
    byId('adminNotificationUnread').textContent =
      `${unreadCount.toLocaleString()} unread`;
    byId('markAllAdminNotificationsRead').disabled = unreadCount === 0;
    trigger.setAttribute(
      'aria-label',
      unreadCount ? `${unreadCount} unread admin alerts` : 'Admin alerts, none unread'
    );
  }

  function renderAdminNotifications(items = state.notificationItems) {
    const list = byId('adminNotificationList');
    const empty = byId('adminNotificationEmpty');
    const filteredItems = state.notificationFilter === 'all'
      ? items
      : items.filter((item) => state.notificationFilter === 'moderation'
        ? ['moderation', 'creator'].includes(item.category)
        : item.category === state.notificationFilter);
    list.innerHTML = '';
    list.classList.toggle('hidden', filteredItems.length === 0);
    empty.classList.toggle('hidden', filteredItems.length > 0);
    if (!filteredItems.length) return;

    list.innerHTML = filteredItems.map((item) => {
      const severity = ['critical', 'warning', 'info'].includes(item.severity)
        ? item.severity
        : 'info';
      const readClass = item.is_read ? '' : ' is-unread';
      const unreadDot = item.is_read
        ? ''
        : '<span class="admin-notification-unread-dot" aria-label="Unread"></span>';
      return `<a class="admin-notification-card severity-${severity}${readClass}"
          href="${escapeHtml(notificationTargetUrl(item.target_url))}"
          data-notification-id="${escapeHtml(item._id)}" role="listitem">
        <span class="admin-notification-severity">${escapeHtml(severity)}</span>
        <div class="admin-notification-card-copy">
          <div class="admin-notification-card-heading">
            <strong>${escapeHtml(item.title)}</strong>${unreadDot}
          </div>
          <p>${escapeHtml(item.message)}</p>
          <div class="admin-notification-card-footer">
            <span>${escapeHtml(notificationCategoryLabel(item.category))}</span>
            <time>${escapeHtml(relativeNotificationTime(item.created_at))}</time>
          </div>
          <span class="admin-notification-action">${escapeHtml(notificationActionLabel(item.category))} <i class="fas fa-arrow-right" aria-hidden="true"></i></span>
        </div>
      </a>`;
    }).join('');
  }

  async function refreshAdminNotifications() {
    const list = byId('adminNotificationList');
    list.setAttribute('aria-busy', 'true');
    try {
      const data = await api('/notifications', {}, '/api/admin');
      state.notificationItems = data.items || [];
      updateNotificationUnreadCount(data.unread_count);
      renderAdminNotifications();
      state.notificationUnavailable = false;
    } catch (error) {
      list.innerHTML = '<p class="admin-notification-empty">Admin alerts could not be loaded. Please try again.</p>';
      list.classList.remove('hidden');
      byId('adminNotificationEmpty').classList.add('hidden');
      if (!state.notificationUnavailable) showToast(error.message, 'error');
      state.notificationUnavailable = true;
    } finally {
      list.setAttribute('aria-busy', 'false');
    }
  }

  async function markAdminNotificationRead(notificationId) {
    const body = notificationId ? { notification_id: notificationId } : {};
    const data = await api('/notifications/mark-read', {
      method: 'POST',
      body: JSON.stringify(body),
    }, '/api/admin');
    updateNotificationUnreadCount(data.unread_count);
    await refreshAdminNotifications();
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
    rows.innerHTML = items.length ? items.map((user) => `<tr>
      <td><span class="user-id-value" title="${escapeHtml(user.id)}">${escapeHtml(user.id.slice(0, 10))}…</span></td>
      <td><span class="user-email-value" title="${escapeHtml(user.email)}">${escapeHtml(user.email)}</span></td>
      <td><span class="user-role-pill ${user.role === 'Admin' ? 'admin' : user.role === 'Creator' ? 'creator' : 'parent'}">${escapeHtml(user.role)}</span></td>
      <td><span class="user-profile-count">${escapeHtml(user.children)}</span></td>
      <td><span class="status-pill ${statusClass(user.status)}"><span class="user-status-dot" aria-hidden="true"></span>${escapeHtml(titleCase(user.status))}</span></td>
      <td><div class="table-actions user-table-actions">
        <button class="table-action reset-pin" type="button" data-id="${escapeHtml(user.id)}" aria-label="Send PIN reset to ${escapeHtml(user.email)}" ${user.role === 'Admin' ? 'disabled' : ''}><i class="fas fa-key" aria-hidden="true"></i><span>Reset PIN</span></button>
        <button class="table-action toggle-status" type="button" data-id="${escapeHtml(user.id)}" data-status="${user.status === 'suspended' ? 'active' : 'suspended'}" aria-label="${user.status === 'suspended' ? 'Unsuspend' : 'Suspend'} ${escapeHtml(user.email)}" ${user.id === activeAdminId ? 'disabled' : ''}><i class="fas ${user.status === 'suspended' ? 'fa-user-check' : 'fa-user-slash'}" aria-hidden="true"></i><span>${user.status === 'suspended' ? 'Unsuspend' : 'Suspend'}</span></button>
        ${user.id === activeAdminId ? '' : `<button class="table-action danger delete-user" type="button" data-id="${escapeHtml(user.id)}" aria-label="Remove ${escapeHtml(user.email)}"><i class="fas fa-trash" aria-hidden="true"></i><span>Remove</span></button>`}
      </div></td>
    </tr>`).join('') :
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
      if (state.analytics) renderAnalytics(state.analytics);
    });
    byId('adminNotifications').addEventListener('click', async () => {
      const dropdown = byId('adminNotificationDropdown');
      const open = dropdown.classList.toggle('hidden') === false;
      byId('adminNotifications').setAttribute('aria-expanded', String(open));
      if (open) await refreshAdminNotifications();
    });
    document.querySelectorAll('[data-notification-filter]').forEach((button) => {
      button.addEventListener('click', () => {
        state.notificationFilter = button.dataset.notificationFilter;
        document.querySelectorAll('[data-notification-filter]').forEach((filterButton) => {
          const selected = filterButton === button;
          filterButton.classList.toggle('active', selected);
          filterButton.setAttribute('aria-pressed', String(selected));
        });
        renderAdminNotifications();
      });
    });
    byId('markAllAdminNotificationsRead').addEventListener('click', async () => {
      try {
        await markAdminNotificationRead();
      } catch (error) {
        showToast(error.message, 'error');
      }
    });
    byId('adminNotificationList').addEventListener('click', async (event) => {
      const card = event.target.closest('.admin-notification-card');
      if (!card) return;
      event.preventDefault();
      const targetUrl = notificationTargetUrl(card.getAttribute('href'));
      try {
        await markAdminNotificationRead(card.dataset.notificationId);
        window.location.assign(targetUrl);
      } catch (error) {
        showToast(error.message, 'error');
      }
    });
    byId('analyticsRange').addEventListener('change', () => {
      loadAnalytics().catch(handleAnalyticsError);
    });
    byId('viewingSilo').addEventListener('change', () => {
      loadPlatformViewingAnalytics().catch(handleAnalyticsError);
    });
    document.querySelectorAll('[data-activity-view]').forEach((button) => {
      button.addEventListener('click', () => {
        state.activityView = button.dataset.activityView;
        if (state.analytics) renderActivityChart(state.analytics.activity || {}, state.activityView);
      });
    });
    byId('sidebarToggle').addEventListener('click', () => {
      byId('sidebarDrawer').classList.contains('open')
        ? closeAdminSidebar()
        : openAdminSidebar();
    });
    byId('sidebarBackdrop').addEventListener('click', closeAdminSidebar);
    byId('adminProfileToggle').addEventListener('click', () => {
      const menu = byId('adminProfileMenu');
      const open = menu.classList.toggle('hidden') === false;
      byId('adminProfileToggle').setAttribute('aria-expanded', String(open));
    });
    document.addEventListener('click', (event) => {
      if (!event.target.closest('.admin-profile')) closeAdminProfileMenu();
      if (!event.target.closest('.admin-notification-center')) {
        byId('adminNotificationDropdown').classList.add('hidden');
        byId('adminNotifications').setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeAdminProfileMenu();
        closeAdminSidebar();
        byId('adminNotificationDropdown').classList.add('hidden');
        byId('adminNotifications').setAttribute('aria-expanded', 'false');
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
    const savedTheme = localStorage.getItem('spectra_theme') || 'light';
    document.documentElement.dataset.theme = savedTheme;
    byId('themeIcon').className = savedTheme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
    byId('themeToggleBtn').setAttribute('aria-pressed', String(savedTheme === 'dark'));
    byId('themeToggleBtn').setAttribute('aria-label', `Switch to ${savedTheme === 'light' ? 'dark' : 'light'} theme`);
    if (sessionStorage.getItem(sidebarStorageKey) === 'true') {
      openAdminSidebar();
    } else {
      closeAdminSidebar();
    }
    try {
      await Promise.all([loadOverview(), loadMetrics(), loadAnalytics(), loadFeedback(), loadPolicies(), loadUsers()]);
    } catch (error) { showToast(error.message, 'error'); }
    if (new URLSearchParams(window.location.search).has('creator_id')) {
      window.history.replaceState(null, '', `${window.location.pathname}#users`);
      switchTab('users');
    }
    refreshAdminNotifications();
    window.setInterval(refreshDashboard, 30000);
    window.setInterval(refreshMetrics, 5000);
    window.setInterval(() => loadFeedback().catch((error) => showToast(error.message, 'error')), 30000);
    window.setInterval(refreshAdminNotifications, 12000);
    window.setInterval(() => {
      if (byId('analytics-panel').classList.contains('active')) {
        loadAnalytics().catch(handleAnalyticsError);
      }
    }, 30000);
  });

})();
