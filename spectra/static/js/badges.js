const achievementGrid = document.getElementById('achievementGrid');
const achievementToast = document.getElementById('achievementToast');
const achievementIcons = {
  sparkles: 'fa-wand-magic-sparkles',
  film: 'fa-film',
  users: 'fa-users',
  'shield-check': 'fa-shield-halved',
  star: 'fa-star',
  certificate: 'fa-award',
  eye: 'fa-eye',
  lock: 'fa-lock',
};
const knownUnlockedBadges = new Set(
  [...(achievementGrid?.querySelectorAll('.achievement-card:not(.is-locked)') || [])]
    .map((card) => card.dataset.badgeId)
);
const toastQueue = [];
let toastActive = false;

function appendAchievementIcon(parent, iconName, className = '') {
  const icon = document.createElement('i');
  icon.className = `fas ${achievementIcons[iconName] || 'fa-award'} ${className}`.trim();
  icon.setAttribute('aria-hidden', 'true');
  parent.appendChild(icon);
}

function renderAchievementCard(badge) {
  const card = document.createElement('article');
  card.className = `achievement-card${badge.unlocked ? '' : ' is-locked'}`;
  card.dataset.badgeId = badge.badge_id;

  const iconWrap = document.createElement('div');
  iconWrap.className = 'achievement-icon-wrap';
  appendAchievementIcon(iconWrap, badge.icon);
  if (!badge.unlocked) {
    const lock = document.createElement('span');
    lock.className = 'achievement-lock';
    appendAchievementIcon(lock, 'lock');
    iconWrap.appendChild(lock);
  }
  card.appendChild(iconWrap);

  const content = document.createElement('div');
  content.className = 'achievement-card-content';
  const category = document.createElement('span');
  category.className = 'achievement-category';
  category.textContent = badge.category;
  const title = document.createElement('h3');
  title.textContent = badge.name;
  const description = document.createElement('p');
  description.textContent = badge.description;
  content.append(category, title, description);

  if (badge.unlocked) {
    const unlocked = document.createElement('p');
    unlocked.className = 'achievement-unlocked';
    const date = badge.unlocked_at ? new Date(badge.unlocked_at) : null;
    unlocked.textContent = `Unlocked ${date && !Number.isNaN(date.valueOf())
      ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
      : ''}`;
    content.appendChild(unlocked);
  } else {
    const progressLabel = document.createElement('div');
    progressLabel.className = 'achievement-progress-label';
    const progressText = document.createElement('span');
    progressText.textContent = badge.progress_text;
    const percentage = document.createElement('span');
    percentage.textContent = `${badge.progress_pct}%`;
    progressLabel.append(progressText, percentage);

    const progress = document.createElement('div');
    progress.className = 'progress achievement-progress';
    progress.setAttribute('role', 'progressbar');
    progress.setAttribute('aria-label', `${badge.name} progress`);
    progress.setAttribute('aria-valuenow', String(badge.progress_pct));
    progress.setAttribute('aria-valuemin', '0');
    progress.setAttribute('aria-valuemax', '100');
    const bar = document.createElement('div');
    bar.className = 'progress-bar';
    bar.style.width = `${badge.progress_pct}%`;
    progress.appendChild(bar);
    content.append(progressLabel, progress);
  }

  card.appendChild(content);
  return card;
}

function updateAchievementStats(metrics) {
  const values = {
    uploads: metrics.total_uploads,
    subscribers: metrics.total_subscribers,
    views: metrics.total_views,
    streak: metrics.clean_streak,
  };
  Object.entries(values).forEach(([key, value]) => {
    const output = document.querySelector(`[data-achievement-stat="${key}"]`);
    if (output) output.textContent = Number(value || 0).toLocaleString();
  });
}

function showNextAchievementToast() {
  if (!achievementToast || toastActive || !toastQueue.length) return;
  toastActive = true;
  const badge = toastQueue.shift();
  achievementToast.replaceChildren();
  appendAchievementIcon(achievementToast, badge.icon, 'achievement-toast-icon');
  const message = document.createElement('span');
  message.textContent = `Badge unlocked: ${badge.name}`;
  achievementToast.appendChild(message);
  achievementToast.classList.add('is-visible');
  window.setTimeout(() => {
    achievementToast.classList.remove('is-visible');
    window.setTimeout(() => {
      toastActive = false;
      showNextAchievementToast();
    }, 250);
  }, 4200);
}

async function refreshAchievements() {
  if (!achievementGrid) return;
  try {
    const response = await fetch(achievementGrid.dataset.achievementsUrl, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    if (!response.ok) throw new Error(`Achievement refresh failed (${response.status}).`);
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'Unable to refresh achievements.');

    data.badges.forEach((badge) => {
      if (badge.unlocked && !knownUnlockedBadges.has(badge.badge_id)) {
        knownUnlockedBadges.add(badge.badge_id);
        toastQueue.push(badge);
      }
    });
    achievementGrid.replaceChildren(...data.badges.map(renderAchievementCard));
    updateAchievementStats(data.metrics);
    showNextAchievementToast();
  } catch (error) {
    console.error('Unable to refresh achievements:', error);
  }
}

refreshAchievements();
window.setInterval(refreshAchievements, 12000);
