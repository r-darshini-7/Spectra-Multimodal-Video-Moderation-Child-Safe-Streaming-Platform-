(() => {
  const alertEl = document.getElementById('profileAlert');
  const byId = (id) => document.getElementById(id);
  const settingsSearch = byId('settingsSearch');
  const settingCards = [...document.querySelectorAll('.settings-card')];
  const settingsLinks = [...document.querySelectorAll('.settings-nav a')];
  let activeSectionId = settingCards[0]?.id;

  function showSection(sectionId) {
    const selectedCard = settingCards.find((card) => card.id === sectionId);
    if (!selectedCard) return;
    activeSectionId = sectionId;
    settingsLinks.forEach((link) => {
      const active = link.getAttribute('href') === `#${sectionId}`;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
    settingCards.forEach((card) => { card.hidden = card !== selectedCard; });
  }

  function updateSearchResults() {
    if (!settingsSearch) return;
    const query = settingsSearch.value.trim().toLocaleLowerCase();
    let visibleCount = 0;
    settingCards.forEach((card) => {
      const matches = !query || card.textContent.toLocaleLowerCase().includes(query);
      const visible = query ? matches : card.id === activeSectionId;
      card.hidden = !visible;
      if (visible) visibleCount += 1;
      const link = settingsLinks.find((item) => item.getAttribute('href') === `#${card.id}`);
      if (link) link.hidden = !matches;
    });
    byId('settingsNoResults')?.classList.toggle('d-none', visibleCount !== 0);
  }

  settingsLinks.forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      settingsSearch.value = '';
      showSection(link.getAttribute('href').slice(1));
      updateSearchResults();
      window.history.pushState(null, '', link.getAttribute('href'));
    });
  });
  window.addEventListener('popstate', () => {
    showSection(window.location.hash.slice(1) || settingCards[0]?.id);
    updateSearchResults();
  });
  showSection(window.location.hash.slice(1) || activeSectionId);
  settingsSearch?.addEventListener('input', updateSearchResults);

  updateSearchResults();

  function showAlert(message, type = 'success') {
    if (!alertEl) return;
    alertEl.className = `alert alert-${type} settings-alert`;
    alertEl.textContent = message;
    alertEl.classList.remove('d-none');
    window.clearTimeout(showAlert.timer);
    showAlert.timer = window.setTimeout(() => alertEl.classList.add('d-none'), 6000);
  }

  async function requestJson(url, payload, method = 'POST') {
    const response = await fetch(url, {
      method,
      headers: {'Content-Type': 'application/json'},
      body: payload ? JSON.stringify(payload) : undefined,
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || `Request failed (${response.status}).`);
    }
    return result;
  }

  function handleFailure(error) {
    showAlert(error.message || 'The request could not be completed.', 'danger');
  }

  byId('saveProfileDetails')?.addEventListener('click', async () => {
    try {
      const result = await requestJson('/auth/profile/update', {
        name: byId('profileName').value.trim(),
        age_group: byId('profileAgeGroup').value,
      });
      showAlert(result.message);
    } catch (error) { handleFailure(error); }
  });

  byId('profileImage')?.addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 3 * 1024 * 1024) {
      showAlert('Choose a PNG, JPG, or WEBP image smaller than 3 MB.', 'warning');
      event.target.value = '';
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const result = await requestJson('/auth/profile/avatar', {avatar_data: reader.result});
        byId('profileAvatarPreview').innerHTML = `<img src="${reader.result}" alt="Primary profile photo"/>`;
        const navAvatar = document.querySelector('.nav-avatar');
        if (navAvatar) navAvatar.innerHTML = `<img src="${reader.result}" alt=""/>`;
        showAlert(result.message);
      } catch (error) { handleFailure(error); }
    };
    reader.onerror = () => showAlert('The selected image could not be read.', 'danger');
    reader.readAsDataURL(file);
  });

  byId('verifyPasswordButton')?.addEventListener('click', async () => {
    try {
      const result = await requestJson('/auth/profile/password/verify', {
        password: byId('verifyPassword').value,
      });
      byId('passwordVerifiedMessage').textContent = result.message;
      byId('passwordVerifiedMessage').classList.remove('d-none');
    } catch (error) { handleFailure(error); }
  });

  byId('updatePasswordButton')?.addEventListener('click', async () => {
    try {
      const result = await requestJson('/auth/profile/password', {
        old_password: byId('oldPassword').value,
        new_password: byId('newPassword').value,
        confirm_password: byId('confirmPassword').value,
      });
      ['oldPassword', 'newPassword', 'confirmPassword'].forEach((id) => { byId(id).value = ''; });
      showAlert(result.message);
    } catch (error) { handleFailure(error); }
  });

  byId('requestPasswordResetLink')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await requestJson('/auth/profile/recovery/request', {});
      showAlert(result.message);
    } catch (error) {
      handleFailure(error);
    } finally {
      button.disabled = false;
    }
  });

  byId('showAddMember')?.addEventListener('click', () => {
    byId('addMemberForm').classList.toggle('d-none');
    byId('addMemberForm').scrollIntoView({behavior: 'smooth', block: 'center'});
  });

  byId('addMemberButton')?.addEventListener('click', async () => {
    try {
      const payload = {
        name: byId('memberName').value.trim(),
        age_group: byId('memberAgeGroup').value,
        pin: byId('memberPasskey').value,
        password: byId('memberAccountPassword').value,
      };
      const avatar = await readMemberPhoto(byId('newMemberPhoto'));
      if (avatar) payload.avatar_data = avatar;
      const result = await requestJson('/auth/profile/members', payload);
      showAlert(result.message);
      window.setTimeout(() => window.location.reload(), 600);
    } catch (error) { handleFailure(error); }
  });

  async function readMemberPhoto(input) {
    const file = input.files?.[0];
    if (!file) return undefined;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 3 * 1024 * 1024) {
      throw new Error('Choose a PNG, JPG, or WEBP image smaller than 3 MB.');
    }
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('The selected image could not be read.'));
      reader.readAsDataURL(file);
    });
  }

  document.querySelectorAll('.save-member').forEach((button) => {
    button.addEventListener('click', async () => {
      const form = button.closest('.family-edit-form');
      const payload = {
        name: form.querySelector('.member-name').value.trim(),
        age_group: form.querySelector('.member-age').value,
        password: form.querySelector('.member-password').value,
      };
      const newPin = form.querySelector('.member-pin').value;
      if (newPin) payload.pin = newPin;
      try {
        const avatar = await readMemberPhoto(form.querySelector('.member-photo'));
        if (avatar) payload.avatar_data = avatar;
        const result = await requestJson(`/auth/profile/members/${encodeURIComponent(form.dataset.memberId)}`, payload, 'PATCH');
        showAlert(result.message);
        window.setTimeout(() => window.location.reload(), 600);
      } catch (error) { handleFailure(error); }
    });
  });

  document.querySelectorAll('.delete-member').forEach((button) => {
    button.addEventListener('click', async () => {
      const form = button.closest('.family-member-card').querySelector('.family-edit-form');
      if (!window.confirm('Delete this family member and their profile history?')) return;
      try {
        const result = await requestJson(
          `/auth/profile/members/${encodeURIComponent(form.dataset.memberId)}`,
          {password: form.querySelector('.member-password').value},
          'DELETE',
        );
        showAlert(result.message);
        window.setTimeout(() => window.location.reload(), 600);
      } catch (error) { handleFailure(error); }
    });
  });

  async function savePreferences(payload) {
    const result = await requestJson('/auth/profile/settings', payload);
    if (Object.hasOwn(payload, 'theme')) applyUserThemePreference(payload.theme);
    showAlert('Settings saved.');
    return result;
  }

  byId('themePreference')?.addEventListener('change', (event) => {
    savePreferences({theme: event.target.value}).catch(handleFailure);
  });
  byId('notificationsEnabled')?.addEventListener('change', (event) => {
    savePreferences({notifications_enabled: event.target.checked}).catch(handleFailure);
  });
  byId('watchLimit')?.addEventListener('change', (event) => {
    savePreferences({watch_limit_minutes: Number(event.target.value)}).catch(handleFailure);
  });

  byId('historyCategoryFilter')?.addEventListener('change', (event) => {
    const category = event.target.value;
    let visible = 0;
    document.querySelectorAll('.settings-history-item').forEach((item) => {
      const match = !category || item.dataset.category === category;
      item.classList.toggle('d-none', !match);
      if (match) visible += 1;
    });
    byId('noFilteredHistory')?.classList.toggle('d-none', visible !== 0);
  });

  byId('clearHistoryButton')?.addEventListener('click', async () => {
    if (!window.confirm('Delete all watch history for this profile?')) return;
    try {
      const result = await requestJson('/history/clear', {});
      showAlert(result.message);
      window.setTimeout(() => window.location.reload(), 600);
    } catch (error) { handleFailure(error); }
  });

  byId('deleteAccountButton')?.addEventListener('click', async () => {
    if (!byId('confirmDeleteAccount').checked) {
      showAlert('Confirm that you understand this action is permanent.', 'warning');
      return;
    }
    if (!window.confirm('Permanently delete your household account? This cannot be undone.')) return;
    try {
      const result = await requestJson('/auth/profile/delete', {
        password: byId('deleteAccountPassword').value,
      });
      window.location.assign(result.redirect);
    } catch (error) { handleFailure(error); }
  });
})();
