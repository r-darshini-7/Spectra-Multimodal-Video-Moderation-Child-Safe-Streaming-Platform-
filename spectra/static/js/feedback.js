(() => {
  const forms = [...document.querySelectorAll('[data-feedback-form]')];
  const feedbackModal = document.getElementById('feedbackModal');
  const ratingLabels = {
    1: 'Poor',
    2: 'Fair',
    3: 'Good',
    4: 'Very good',
    5: 'Excellent',
  };

  if (!forms.length) return;

  function setError(form, message) {
    const error = form.querySelector('[data-feedback-error]');
    error.textContent = message;
    error.hidden = !message;
  }

  function updateIssueOptions(form) {
    const category = form.querySelector('[name="category"]').value;
    const fieldset = form.querySelector('[data-feedback-issues]');
    const groups = [...fieldset.querySelectorAll('[data-issue-category]')];
    const active = groups.find(group => group.dataset.issueCategory === category);
    fieldset.hidden = !active;
    groups.forEach(group => {
      const visible = group === active;
      group.hidden = !visible;
      if (!visible) group.querySelectorAll('input[type="checkbox"]').forEach(input => { input.checked = false; });
    });
  }

  function updateRating(form, preview = 0) {
    const selected = Number(form.querySelector('.feedback-rating-input:checked')?.value || 0);
    const shown = preview || selected;
    const labels = [...form.querySelectorAll('.feedback-rating-star')];
    labels.forEach((label, index) => {
      label.classList.toggle('is-active', index < selected);
      label.classList.toggle('is-preview', Boolean(preview) && index < preview);
    });
    const text = form.querySelector('[data-rating-label]');
    text.textContent = shown ? ratingLabels[shown] : 'Choose a rating';
  }

  function resetForm(form) {
    form.reset();
    form.querySelector('[data-feedback-fields]').hidden = false;
    form.querySelector('[data-feedback-success]').hidden = true;
    const submit = form.querySelector('.feedback-submit');
    submit.disabled = false;
    submit.classList.remove('is-loading');
    form.querySelector('[data-submit-label]').textContent = 'Send feedback';
    setError(form, '');
    updateIssueOptions(form);
    updateRating(form);
    const description = form.querySelector('[name="description"]');
    description.value = '';
    description.dispatchEvent(new Event('input', {bubbles: true}));
  }

  function getCurrentPlaybackContext() {
    const url = new URL(window.location.href);
    if (!url.pathname.startsWith('/watch')) return null;
    const player = window.videojs?.getPlayer('spectaPlayer');
    const video = document.querySelector('#spectaPlayer video, video#spectaPlayer');
    let timestamp = null;
    try {
      const currentTime = player?.currentTime?.() ?? video?.currentTime;
      if (Number.isFinite(currentTime) && currentTime >= 0) timestamp = currentTime;
    } catch {}
    return {videoContext: url.href, videoId: url.searchParams.get('v') || '', timestamp};
  }

  forms.forEach(form => {
    const category = form.querySelector('[name="category"]');
    const description = form.querySelector('[name="description"]');
    const counter = form.querySelector('[data-character-count]');
    const ratingInputs = [...form.querySelectorAll('.feedback-rating-input')];
    const ratingStars = [...form.querySelectorAll('.feedback-rating-star')];

    category.addEventListener('change', () => {
      updateIssueOptions(form);
      setError(form, '');
    });

    description.addEventListener('input', () => {
      const length = description.value.length;
      counter.textContent = `${length} / 1000`;
      counter.classList.toggle('is-limit', length >= 950);
      if (length >= 10) setError(form, '');
    });

    ratingInputs.forEach(input => input.addEventListener('change', () => updateRating(form)));
    ratingStars.forEach((star, index) => {
      star.addEventListener('mouseenter', () => updateRating(form, index + 1));
      star.addEventListener('focus', () => updateRating(form, index + 1));
      star.addEventListener('blur', () => updateRating(form));
    });
    form.querySelector('.feedback-rating')?.addEventListener('mouseleave', () => updateRating(form));

    form.addEventListener('submit', async event => {
      event.preventDefault();
      setError(form, '');
      const text = description.value.trim();
      if (!category.value) {
        setError(form, 'Choose a feedback category.');
        category.focus();
        return;
      }
      if (text.length < 10 || text.length > 1000) {
        setError(form, 'Description must be between 10 and 1000 characters.');
        description.focus();
        return;
      }

      const video = document.querySelector('#spectaPlayer video, video#spectaPlayer');
      const diagnosticsEnabled = form.querySelector('[name="include_diagnostics"]').checked;
      const rating = form.querySelector('.feedback-rating-input:checked')?.value || null;
      const timestampValue = form.querySelector('[name="timestamp_marker"]').value;
      const resolution = video?.videoWidth && video?.videoHeight
        ? `${video.videoWidth}x${video.videoHeight}`
        : `${window.screen.width}x${window.screen.height}`;
      const payload = {
        category: category.value,
        video_context: form.querySelector('[name="video_context"]').value.trim(),
        video_id: new URL(form.querySelector('[name="video_context"]').value || window.location.href, window.location.origin).searchParams.get('v') || '',
        timestamp_marker: timestampValue || null,
        issue_tags: [...form.querySelectorAll('[name="issue_tags"]:checked')].map(input => input.value),
        rating,
        description: text,
        include_diagnostics: diagnosticsEnabled,
        resolution: diagnosticsEnabled ? resolution : null,
        hls_latency: null,
      };

      const submit = form.querySelector('.feedback-submit');
      submit.disabled = true;
      submit.classList.add('is-loading');
      form.querySelector('[data-submit-label]').textContent = 'Sending...';
      try {
        const response = await fetch('/api/feedback', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.error || 'Feedback could not be sent. Please try again.');
        form.querySelector('[data-feedback-fields]').hidden = true;
        const success = form.querySelector('[data-feedback-success]');
        success.hidden = false;
        success.focus();
      } catch (error) {
        setError(form, error.message || 'Connection error. Please try again.');
        submit.disabled = false;
      } finally {
        submit.classList.remove('is-loading');
        form.querySelector('[data-submit-label]').textContent = 'Send feedback';
      }
    });

    updateIssueOptions(form);
    updateRating(form);
  });

  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-feedback-open]');
    if (!trigger || !feedbackModal || !window.bootstrap?.Modal) return;
    event.preventDefault();
    window.bootstrap.Modal.getOrCreateInstance(feedbackModal).show(trigger);
  });

  feedbackModal?.addEventListener('show.bs.modal', event => {
    const form = feedbackModal.querySelector('[data-feedback-form]');
    if (!form) return;
    resetForm(form);
    if (event.relatedTarget?.dataset.feedbackContext !== 'current-video') return;
    const context = getCurrentPlaybackContext();
    if (!context) return;
    form.querySelector('[name="video_context"]').value = context.videoContext;
    if (context.timestamp !== null) form.querySelector('[name="timestamp_marker"]').value = context.timestamp.toFixed(1);
  });

  feedbackModal?.addEventListener('hidden.bs.modal', () => {
    const form = feedbackModal.querySelector('[data-feedback-form]');
    if (form) resetForm(form);
  });
})();