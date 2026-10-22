const analyticsCharts = {
  activity: null,
  topics: null,
  monthly: null,
  weekly: null,
  timeOfDay: null,
  achievements: null,
};

const analyticsPeriod = document.getElementById('analyticsPeriod');
const analyticsAgeGroup = document.getElementById('analyticsAgeGroup');
let analyticsRequestController = null;
analyticsPeriod?.addEventListener('change', loadViewerAnalytics);
analyticsAgeGroup?.addEventListener('change', loadViewerAnalytics);
loadViewerAnalytics();

async function loadViewerAnalytics() {
  const errorElement = document.getElementById('analyticsError');
  errorElement?.classList.add('d-none');
  analyticsRequestController?.abort();
  analyticsRequestController = new AbortController();
  try {
    const params = new URLSearchParams({
      months: analyticsPeriod?.value || '1',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    });
    if (analyticsAgeGroup) params.set('age_group', analyticsAgeGroup.value);
    const response = await fetch(`/analytics/data?${params.toString()}`, {
      cache: 'no-store',
      signal: analyticsRequestController.signal,
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || 'Could not load analytics.');
    }

    renderSummary(data);
    renderVideoActivity(data.counts);
    renderTopicInterests(data.topic_interests);
    renderMonthlyWatchTime(data.watch_time_by_month);
    renderWeeklyWatchTime(data.watch_by_day);
    renderTimeOfDay(data.time_of_day);
    renderCreators(data.top_creators);
    renderSavedProgress(data.saved_vs_watched);
    renderBadges(data.badges);
    renderAchievementAnalytics(
      data.achievement_analytics,
      data.show_achievement_analytics
    );
  } catch (error) {
    if (error.name === 'AbortError') return;
    if (errorElement) {
      errorElement.textContent = error.message || 'Could not load analytics.';
      errorElement.classList.remove('d-none');
    }
  }
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;
  const parts = [];
  if (hours) parts.push(`${hours} hr${hours === 1 ? '' : 's'}`);
  if (minutes) parts.push(`${minutes} min${minutes === 1 ? '' : 's'}`);
  if (remainingSeconds || !parts.length) {
    parts.push(`${remainingSeconds} sec${remainingSeconds === 1 ? '' : 's'}`);
  }
  return parts.join(' ');
}

function setEmptyState(canvas, emptyMessage, hasData) {
  if (!canvas || !emptyMessage) return false;
  canvas.classList.toggle('d-none', !hasData);
  emptyMessage.classList.toggle('d-none', hasData);
  return hasData;
}

function destroyChart(key) {
  analyticsCharts[key]?.destroy();
  analyticsCharts[key] = null;
}

function renderSummary(data) {
  const summary = data.summary || {};
  const total = document.getElementById('totalScreenTime');
  const average = document.getElementById('dailyAverage');
  const status = document.getElementById('viewingStatus');
  const topic = document.getElementById('topTopic');

  if (total) total.textContent = formatDuration(summary.total_seconds || 0);
  if (average) average.textContent = `${formatDuration(summary.daily_average_seconds || 0)}/day`;
  if (status) {
    const avgMinutes = Number(summary.daily_average_seconds || 0) / 60;
    status.textContent = avgMinutes <= 60 ? 'Balanced' : avgMinutes <= 120 ? 'Moderate' : 'Take a break';
    status.style.background = avgMinutes <= 120 ? '#e9f9ef' : '#fff5df';
    status.style.color = avgMinutes <= 120 ? '#15803d' : '#a16207';
  }
  if (topic) topic.textContent = summary.top_topic || 'Not enough history yet';

  const favorite = summary.most_replayed;
  const favoriteWrap = document.getElementById('favoriteVideo');
  const favoriteEmpty = document.getElementById('favoriteEmpty');
  if (!favorite || !favoriteWrap || !favoriteEmpty) {
    favoriteWrap?.classList.add('d-none');
    favoriteEmpty?.classList.remove('d-none');
    return;
  }
  favoriteWrap.classList.remove('d-none');
  favoriteEmpty.classList.add('d-none');
  const link = document.getElementById('favoriteTitle');
  const image = document.getElementById('favoriteThumbnail');
  const plays = document.getElementById('favoritePlays');
  if (link) {
    link.textContent = favorite.title || 'Untitled video';
    link.href = `/watch?v=${encodeURIComponent(favorite.video_id)}`;
  }
  if (image && favorite.thumbnail) {
    image.src = `/media/${encodeURIComponent(favorite.video_id)}/thumbnails/${encodeURIComponent(favorite.thumbnail)}`;
    image.alt = '';
    image.classList.remove('d-none');
  } else {
    image?.classList.add('d-none');
  }
  if (plays) {
    const count = Number(favorite.plays || 0);
    plays.textContent = `${count} ${count === 1 ? 'play' : 'plays'} in this period`;
  }
}

function renderMonthlyWatchTime(months) {
  const canvas = document.getElementById('watchTimeChart');
  const emptyMessage = document.getElementById('watchTimeEmpty');
  if (!canvas || !emptyMessage) return;
  destroyChart('monthly');
  if (!setEmptyState(
    canvas,
    emptyMessage,
    months?.some((month) => Number(month.seconds) > 0),
  )) return;

  analyticsCharts.monthly = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: months.map((month) => month.month),
      datasets: [{
        label: 'Hours watched',
        data: months.map((month) => month.hours),
        backgroundColor: 'rgba(92, 69, 253, .82)',
        hoverBackgroundColor: '#7b61ff',
        borderRadius: 8,
        maxBarThickness: 44,
      }],
    },
    options: {
      maintainAspectRatio: false,
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (context) => formatDuration(context.raw * 3600),
          },
        },
      },
      scales: {
        x: { ticks: { color: '#64748b' }, grid: { display: false } },
        y: {
          beginAtZero: true,
          ticks: { color: '#64748b', callback: (value) => `${value}h` },
          grid: { color: '#eef0f5' },
        },
      },
    },
  });
}

function renderWeeklyWatchTime(days) {
  const canvas = document.getElementById('weeklyWatchChart');
  const emptyMessage = document.getElementById('weeklyWatchEmpty');
  if (!canvas || !emptyMessage) return;
  destroyChart('weekly');
  const summary = document.getElementById('weekPatternSummary');
  const hasData = (days || []).some((day) => Number(day.seconds) > 0);
  if (!setEmptyState(canvas, emptyMessage, hasData)) {
    if (summary) summary.textContent = '';
    return;
  }
  const weekdaySeconds = days.slice(0, 5).reduce((total, day) => total + Number(day.seconds || 0), 0);
  const weekendSeconds = days.slice(5).reduce((total, day) => total + Number(day.seconds || 0), 0);
  if (summary) {
    const pattern = weekdaySeconds > weekendSeconds
      ? 'You watched more on weekdays.'
      : weekendSeconds > weekdaySeconds
        ? 'You watched more on the weekend.'
        : 'Your weekday and weekend viewing were about even.';
    summary.textContent = `${pattern} Weekdays ${formatDuration(weekdaySeconds)} · Weekend ${formatDuration(weekendSeconds)}.`;
  }

  analyticsCharts.weekly = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: days.map((day) => day.day),
      datasets: [{
        label: 'Watching time',
        data: days.map((day) => Number((day.seconds / 60).toFixed(1))),
        backgroundColor: ['#d9d3ff', '#d9d3ff', '#d9d3ff', '#d9d3ff', '#d9d3ff', '#5c45fd', '#7b61ff'],
        borderRadius: 7,
        maxBarThickness: 42,
      }],
    },
    options: {
      maintainAspectRatio: false,
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (context) => `${formatDuration(days[context.dataIndex].seconds)} watched`,
          },
        },
      },
      scales: {
        x: { ticks: { color: '#64748b' }, grid: { display: false } },
        y: {
          beginAtZero: true,
          ticks: { color: '#64748b', callback: (value) => `${value}m` },
          grid: { color: '#eef0f5' },
        },
      },
    },
  });
}

function renderTimeOfDay(periods) {
  const canvas = document.getElementById('timeOfDayChart');
  const emptyMessage = document.getElementById('timeOfDayEmpty');
  if (!canvas || !emptyMessage) return;
  destroyChart('timeOfDay');
  const summary = document.getElementById('timeOfDaySummary');
  const hasData = (periods || []).some((period) => Number(period.seconds) > 0);
  if (!setEmptyState(canvas, emptyMessage, hasData)) {
    if (summary) summary.textContent = '';
    return;
  }
  if (summary) {
    summary.textContent = periods
      .filter((period) => Number(period.seconds) > 0)
      .map((period) => `${period.period} ${period.percentage}%`)
      .join(' · ');
  }

  analyticsCharts.timeOfDay = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: periods.map((period) => period.period),
      datasets: [{
        data: periods.map((period) => period.seconds),
        backgroundColor: ['#f59e0b', '#06b6d4', '#7b61ff', '#64748b'],
        borderColor: '#fff',
        borderWidth: 4,
        hoverOffset: 6,
      }],
    },
    options: {
      maintainAspectRatio: false,
      responsive: true,
      cutout: '62%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#64748b', padding: 14, usePointStyle: true, boxWidth: 8 },
        },
        tooltip: {
          callbacks: {
            label: (context) => {
              const period = periods[context.dataIndex];
              return `${period.period}: ${period.percentage}% (${formatDuration(period.seconds)})`;
            },
          },
        },
      },
    },
  });
}

function renderVideoActivity(counts) {
  const canvas = document.getElementById('videoActivityChart');
  const emptyMessage = document.getElementById('videoActivityEmpty');
  if (!canvas || !emptyMessage) return;
  destroyChart('activity');
  const values = [counts.liked, counts.saved, counts.downloaded].map(Number);
  if (!setEmptyState(canvas, emptyMessage, values.some((value) => value > 0))) return;
  analyticsCharts.activity = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: ['Liked', 'Saved', 'Downloaded'],
      datasets: [{
        data: values,
        backgroundColor: ['#7b61ff', '#22c55e', '#06b6d4'],
        borderRadius: 7,
      }],
    },
    options: {
      maintainAspectRatio: false,
      responsive: true,
      scales: {
        x: { ticks: { color: '#64748b' }, grid: { display: false } },
        y: {
          beginAtZero: true,
          ticks: { precision: 0, color: '#64748b' },
          grid: { color: '#eef0f5' },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (context) => `${context.parsed.y} videos` } },
      },
    },
  });
}

function renderTopicInterests(topics) {
  const canvas = document.getElementById('topicInterestsChart');
  const emptyMessage = document.getElementById('topicInterestsEmpty');
  if (!canvas || !emptyMessage) return;
  destroyChart('topics');
  if (!setEmptyState(canvas, emptyMessage, topics?.length > 0)) return;

  analyticsCharts.topics = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: topics.map((topic) => topic.category),
      datasets: [{
        data: topics.map((topic) => topic.count),
        backgroundColor: ['#5c45fd', '#22c55e', '#06b6d4', '#f59e0b', '#ef5da8', '#8b5cf6', '#14b8a6', '#f97316'],
        borderColor: '#fff',
        borderWidth: 3,
      }],
    },
    options: {
      maintainAspectRatio: false,
      responsive: true,
      cutout: '56%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#64748b', padding: 12, usePointStyle: true, boxWidth: 8 },
        },
        tooltip: {
          callbacks: {
            label: (context) => `${context.label}: ${context.parsed} ${context.parsed === 1 ? 'interaction' : 'interactions'}`,
          },
        },
      },
    },
  });
}

function renderCreators(creators) {
  const list = document.getElementById('topCreators');
  const empty = document.getElementById('creatorsEmpty');
  if (!list || !empty) return;
  list.replaceChildren();
  empty.classList.toggle('d-none', creators?.length > 0);
  if (!creators?.length) return;

  creators.forEach((creator, index) => {
    const row = document.createElement('div');
    row.className = 'analytics-creator';
    const rank = document.createElement('span');
    rank.className = 'analytics-creator-rank';
    rank.textContent = `${index + 1}`;
    const name = document.createElement('span');
    name.className = 'analytics-creator-name';
    name.textContent = creator.name;
    const minutes = document.createElement('span');
    minutes.className = 'analytics-creator-minutes';
    minutes.textContent = formatDuration(creator.seconds);
    if (creator.avatar) {
      const avatar = document.createElement('img');
      avatar.className = 'analytics-creator-avatar';
      avatar.src = creator.avatar;
      avatar.alt = '';
      row.append(rank, avatar, name, minutes);
    } else {
      const avatar = document.createElement('span');
      avatar.className = 'analytics-creator-avatar';
      avatar.textContent = (creator.name || 'C').charAt(0).toUpperCase();
      row.append(rank, avatar, name, minutes);
    }
    list.append(row);
  });
}

function renderSavedProgress(saved) {
  const count = document.getElementById('savedWatchedCount');
  const hint = document.getElementById('savedWatchedHint');
  if (count) count.textContent = `${saved?.watched || 0} of ${saved?.saved || 0}`;
  if (hint) {
    hint.textContent = saved?.saved
      ? 'saved videos have also appeared in your watch history'
      : 'Save videos you’d like to come back to.';
  }
}

function renderBadges(badges) {
  const container = document.getElementById('analyticsBadges');
  const empty = document.getElementById('badgesEmpty');
  if (!container || !empty) return;
  container.replaceChildren();
  empty.classList.toggle('d-none', badges?.length > 0);
  if (!badges?.length) return;

  badges.forEach((badge) => {
    const card = document.createElement('div');
    card.className = `analytics-badge ${badge.color || ''}`;
    const icon = document.createElement('span');
    icon.className = 'analytics-badge-icon';
    const iconElement = document.createElement('i');
    iconElement.className = `fas ${badge.icon || 'fa-star'}`;
    icon.append(iconElement);
    const text = document.createElement('div');
    const title = document.createElement('div');
    title.className = 'analytics-badge-title';
    title.textContent = badge.title;
    const detail = document.createElement('div');
    detail.className = 'analytics-badge-detail';
    detail.textContent = badge.detail;
    text.append(title, detail);
    card.append(icon, text);
    container.append(card);
  });
}

function renderAchievementAnalytics(analytics, shouldShow) {
  const section = document.getElementById('achievementAnalyticsSection');
  if (!shouldShow) {
    if (section) section.hidden = true;
    destroyChart('achievements');
    return;
  }
  if (section) section.hidden = false;

  const unlocked = document.getElementById('achievementUnlockedCount');
  const completion = document.getElementById('achievementCompletion');
  const uploads = document.getElementById('achievementUploads');
  const views = document.getElementById('achievementViews');
  const canvas = document.getElementById('achievementProgressChart');
  const empty = document.getElementById('achievementProgressEmpty');
  const badges = analytics?.badges || [];

  if (unlocked) {
    unlocked.textContent = `${Number(analytics?.unlocked_count || 0)} / ${Number(analytics?.total_count || 0)}`;
  }
  if (completion) completion.textContent = `${Number(analytics?.completion_pct || 0)}%`;
  if (uploads) uploads.textContent = Number(analytics?.metrics?.total_uploads || 0).toLocaleString();
  if (views) views.textContent = Number(analytics?.metrics?.total_views || 0).toLocaleString();
  if (!canvas || !empty) return;

  destroyChart('achievements');
  if (!setEmptyState(canvas, empty, badges.length > 0)) return;

  analyticsCharts.achievements = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: badges.map((badge) => badge.name),
      datasets: [{
        data: badges.map((badge) => Number(badge.progress_pct) || 0),
        backgroundColor: badges.map((badge) => (
          badge.unlocked ? 'rgba(34, 197, 94, .82)' : 'rgba(92, 69, 253, .78)'
        )),
        hoverBackgroundColor: badges.map((badge) => (
          badge.unlocked ? '#22c55e' : '#7b61ff'
        )),
        borderRadius: 7,
        maxBarThickness: 22,
      }],
    },
    options: {
      indexAxis: 'y',
      maintainAspectRatio: false,
      responsive: true,
      scales: {
        x: {
          beginAtZero: true,
          max: 100,
          ticks: { color: '#64748b', callback: (value) => `${value}%` },
          grid: { color: '#eef0f5' },
        },
        y: { ticks: { color: '#64748b' }, grid: { display: false } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (context) => {
              const badge = badges[context.dataIndex];
              return `${badge.progress_text}${badge.unlocked ? ' · Unlocked' : ''}`;
            },
          },
        },
      },
    },
  });
}
