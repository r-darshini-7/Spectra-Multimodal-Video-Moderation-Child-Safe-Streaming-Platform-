/**
 * add_video.js — Creator Studio: upload, AI analysis pipeline, gauges, remediation, publish gate
 */

let currentVideoId = null;
let safeGaugeChart = null;
let unsafeGaugeChart = null;
let analysisResult = null;
let lastAnalyzedDetails = null;
let analysisInProgress = false;
let selectedDurationSeconds = 0;
const PUBLISH_SCORE_OVERRIDE_THRESHOLD = 95;
const AGE_GROUP_LABELS = {
  preschool: 'Preschool (0–4)',
  kids: 'Kids (5–8)',
  tweens: 'Tweens (9–12)',
  teens: 'Teens (13–17)',
  unrestricted: 'Unrestricted (18+)',
};

// ══ FILE SELECT ═══════════════════════════════════════════════════════════════

const fileInput = document.getElementById('videoFileInput');
const dropZone  = document.getElementById('dropZone');
const ageSelect = document.getElementById('vAgeSilo');
const categorySelect = document.getElementById('vCategory');
const categoryOptions = JSON.parse(categorySelect?.dataset.categoryOptions || '{}');

fileInput?.addEventListener('change', onFileSelected);

function updateCategories(resetSelection = false) {
  if (!ageSelect || !categorySelect) return;
  updateScoreAgeGroup();
  const selectedCategories = categoryOptions[ageSelect.value] || [];
  const previousCategory = categorySelect.value;
  categorySelect.replaceChildren(...selectedCategories.map((category) => {
    const option = document.createElement('option');
    option.value = category;
    option.textContent = category;
    return option;
  }));
  if (!resetSelection && selectedCategories.includes(previousCategory)) {
    categorySelect.value = previousCategory;
  }
}

function updateScoreAgeGroup() {
  const label = document.getElementById('scoreAgeGroup');
  if (label) {
    label.textContent = AGE_GROUP_LABELS[ageSelect?.value] || 'Select an age group';
  }
}

updateCategories();
ageSelect?.addEventListener('change', () => updateCategories(true));

// Drag & drop
dropZone?.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone?.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone?.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) {
    fileInput.files = e.dataTransfer.files;
    onFileSelected();
  }
});

function onFileSelected() {
  const file = fileInput?.files?.[0];
  if (!file) return;
  selectedDurationSeconds = 0;
  readVideoDuration(file).then((duration) => {
    if (fileInput?.files?.[0] === file) selectedDurationSeconds = duration;
  });
  const nameEl = document.getElementById('fileSelectedName');
  if (nameEl) {
    nameEl.textContent = `✓ ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
    nameEl.classList.remove('d-none');
  }
  document.getElementById('btnUpload').disabled = false;
}

function readVideoDuration(file) {
  return new Promise((resolve) => {
    const video = document.createElement('video');
    const objectUrl = URL.createObjectURL(file);
    let finished = false;
    const finish = (duration = 0) => {
      if (finished) return;
      finished = true;
      clearTimeout(timeout);
      URL.revokeObjectURL(objectUrl);
      resolve(Number.isFinite(duration) ? Math.max(0, Math.floor(duration)) : 0);
    };
    const timeout = setTimeout(() => finish(), 3000);
    video.preload = 'metadata';
    video.onloadedmetadata = () => finish(video.duration);
    video.onerror = () => finish();
    video.src = objectUrl;
  });
}

function setAnalysisButton() {
  const button = document.getElementById('btnUpload');
  if (!button) return;
  button.innerHTML = currentVideoId
    ? '<i class="fas fa-rotate mr-2"></i>Save Details & Re-run Analysis'
    : '<i class="fas fa-rocket mr-2"></i>Upload & Run AI Analysis';
  button.disabled = analysisInProgress || (!currentVideoId && !fileInput?.files?.length);
}

function getVideoDetails() {
  return {
    title: document.getElementById('vTitle')?.value?.trim() || '',
    description: document.getElementById('vDescription')?.value || '',
    age_silo: document.getElementById('vAgeSilo')?.value || 'kids',
    category: document.getElementById('vCategory')?.value || 'General',
  };
}

['vTitle', 'vDescription', 'vAgeSilo', 'vCategory'].forEach((id) => {
  document.getElementById(id)?.addEventListener('input', onVideoDetailsChanged);
  document.getElementById(id)?.addEventListener('change', onVideoDetailsChanged);
});

function onVideoDetailsChanged() {
  setAnalysisButton();
  if (!lastAnalyzedDetails) return;

  const details = getVideoDetails();
  const changed = Object.keys(lastAnalyzedDetails).some(
    (key) => details[key] !== lastAnalyzedDetails[key]
  );
  if (!changed) return;

  analysisResult = null;
  lastAnalyzedDetails = null;
  clearPreviousAnalysis();
  document.getElementById('violationsList').textContent =
    'Details changed. Re-run analysis to update the violations and score.';
  updatePublishGate(null);
  const message = document.getElementById('publishGateMsg');
  if (message) {
    message.textContent = 'Details changed. Re-run analysis before publishing.';
    message.className = 'text-warning small mt-2';
  }
}

// ══ UPLOAD & ANALYSE ═════════════════════════════════════════════════════════

async function uploadAndAnalyze() {
  const details = getVideoDetails();
  if (!details.title) return showAlert('Please enter a video title.', 'warning');

  if (currentVideoId) {
    await runAIAnalysis(currentVideoId, details);
    return;
  }

  const file = fileInput?.files?.[0];
  if (!file) return showAlert('Please select a video file.', 'warning');

  // --- Upload step ---
  analysisInProgress = true;
  setAnalysisButton();
  document.getElementById('uploadProgress')?.classList.remove('d-none');

  const form = new FormData();
  form.append('video_file', file);
  form.append('title', details.title);
  form.append('description', details.description);
  form.append('age_silo', details.age_silo);
  form.append('category', details.category);
  form.append('duration_seconds', String(selectedDurationSeconds));

  try {
    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        document.getElementById('uploadBar').style.width = `${pct}%`;
        document.getElementById('uploadPct').textContent = `${pct}%`;
      }
    };

    const uploadData = await new Promise((resolve, reject) => {
      xhr.open('POST', '/admin/add-video/upload');
      xhr.onload = () => resolve(JSON.parse(xhr.responseText));
      xhr.onerror = reject;
      xhr.send(form);
    });

    if (!uploadData.ok) {
      showAlert(uploadData.error || 'Upload failed.', 'danger');
      return;
    }

    currentVideoId = uploadData.video_id;
    document.getElementById('dropZone')?.classList.add('d-none');
    document.getElementById('uploadedVideoNote')?.classList.remove('d-none');
    document.getElementById('uploadProgress')?.classList.add('d-none');
    setAnalysisButton();
    showAlert('Upload complete. Running AI analysis…', 'info');

    // --- Trigger AI analysis ---
    await runAIAnalysis(currentVideoId);

  } catch (err) {
    showAlert('Upload error: ' + err.message, 'danger');
  } finally {
    analysisInProgress = false;
    setAnalysisButton();
  }
}

// ══ AI PIPELINE ═══════════════════════════════════════════════════════════════

async function runAIAnalysis(videoId, metadata = null) {
  analysisInProgress = true;
  setAnalysisButton();
  if (metadata) clearPreviousAnalysis();
  setAllStepsPending();
  setStepStatus('visual', 'running', metadata ? 'Saving details & checking saved video analysis…' : 'Sampling video frames & running object detection…');
  setStepStatus('audio', 'pending', 'Transcribing audio…');
  setStepStatus('text', 'pending', 'Checking transcript…');
  setStepStatus('rag', 'pending', 'Applying age-tier rules…');

  try {
    const res = await fetch(`/admin/add-video/analyze/${videoId}`, {
      method: 'POST',
      ...(metadata ? {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(metadata),
      } : {}),
    });
    const data = await res.json();

    if (!data.ok) {
      setStepStatus('rag', 'fail', 'Error: ' + (data.error || 'Unknown'));
      return showAlert(data.error || 'Analysis failed.', 'danger');
    }

    analysisResult = data.result;
    lastAnalyzedDetails = getVideoDetails();

    // Update step statuses
    const v = analysisResult.visual;
    const reused = data.reused_analysis;
    setStepStatus('visual', 'done', reused
      ? `Reused saved frame scan · ${v.detected_objects?.length || 0} object class(es)`
      : `Object detections: ${v.detected_objects?.length || 0} class(es); restricted visual themes require review`);
    const transcriptError = analysisResult.transcript?.analysis_error;
    setStepStatus('audio', transcriptError ? 'fail' : 'done', transcriptError
      ? 'Audio transcription unavailable; manual review required.'
      : `${reused ? 'Reused saved' : 'Whisper'} transcript · ${(analysisResult.transcript?.segments||[]).length} segments`);
    const a = analysisResult.audio;
    setStepStatus('text', 'done', `${reused ? 'Reused toxicity results' : 'BERT'} · ${a.violations?.length || 0} flagged segment(s)`);
    setStepStatus('rag', 'done', `Policy verdict: ${analysisResult.policy.verdict.toUpperCase()} · ${analysisResult.citations?.length || 0} clause(s) cited`);

    renderGauges(
      analysisResult.overall_safe_pct,
      analysisResult.overall_unsafe_pct,
      analysisResult.policy.score_basis
    );
    renderVerdict(
      analysisResult.verdict,
      analysisResult.policy.compliance_status,
      analysisResult.overall_safe_pct
    );
    renderViolations(analysisResult.violations_summary);
    renderCitations(analysisResult.citations);
    updatePublishGate(analysisResult);

    showAlert(
      transcriptError
        ? 'Video analysis completed, but audio could not be fully checked. Manual review is required.'
        : reused
          ? 'Details updated. Policy and metadata checks were refreshed using the saved video analysis.'
          : 'AI analysis complete.',
      transcriptError ? 'warning' : analysisResult.verdict === 'safe' ? 'success' : 'warning'
    );

  } catch (err) {
    setStepStatus('rag', 'fail', 'Analysis error: ' + err.message);
    showAlert('AI pipeline error: ' + err.message, 'danger');
  } finally {
    analysisInProgress = false;
    setAnalysisButton();
  }
}

function clearPreviousAnalysis() {
  analysisResult = null;
  lastAnalyzedDetails = null;
  if (safeGaugeChart) safeGaugeChart.destroy();
  if (unsafeGaugeChart) unsafeGaugeChart.destroy();
  safeGaugeChart = null;
  unsafeGaugeChart = null;

  ['safePctLabel', 'unsafePctLabel', 'scoreEvidenceNote'].forEach((id) => {
    const element = document.getElementById(id);
    if (element) element.textContent = '—';
  });

  document.getElementById('verdictBanner')?.classList.add('d-none');
  document.getElementById('violationsList').textContent = 'Analysis in progress…';
  document.getElementById('citationsCard').classList.add('d-none');
  document.getElementById('citationsList').replaceChildren();
  document.getElementById('btnRemediate').style.display = 'none';
  document.getElementById('btnPublish').disabled = true;
  document.getElementById('btnPublish').innerHTML = '<i class="fas fa-rocket mr-2"></i>Publish Video';
  document.getElementById('publishGateMsg').textContent = 'Publishing is locked while the edited details are being analyzed.';
}

// ══ STEP STATUS ═══════════════════════════════════════════════════════════════

function setAllStepsPending() {
  ['visual','audio','text','rag'].forEach(s => setStepStatus(s,'pending','Waiting…'));
}

function setStepStatus(step, state, msg) {
  const icon = document.getElementById(`icon_${step}`);
  const status = document.getElementById(`status_${step}`);
  if (status) status.textContent = msg;
  if (!icon) return;
  icon.className = `step-icon step-${state}`;
  const iconMap = {
    visual: 'fa-eye', audio: 'fa-microphone', text: 'fa-comment', rag: 'fa-book-open'
  };
  const colorMap = {
    pending: 'text-muted', running: 'text-info', done: 'text-success', fail: 'text-danger'
  };
  icon.innerHTML = `<i class="fas ${iconMap[step]} ${colorMap[state] || 'text-muted'}"></i>`;
}

// ══ SPEEDOMETER GAUGES ═══════════════════════════════════════════════════════

function renderGauges(safePct, unsafePct, scoreBasis = {}) {
  const hasScore = Number.isFinite(safePct) && Number.isFinite(unsafePct);
  updateScoreAgeGroup();
  document.getElementById('safePctLabel').textContent = hasScore ? `${safePct}%` : 'N/A';
  document.getElementById('unsafePctLabel').textContent = hasScore ? `${unsafePct}%` : 'N/A';
  const scoreNote = document.getElementById('scoreEvidenceNote');
  if (scoreNote) {
    const ageGroup = AGE_GROUP_LABELS[scoreBasis.age_silo] || 'selected age group';
    scoreNote.textContent = hasScore
      ? `Age-tier score for ${ageGroup}: ${scoreBasis.flagged_frames || 0} of ${scoreBasis.sampled_frames || 0} sampled frames flagged${scoreBasis.audio_applies_to_age_silo ? `; ${scoreBasis.flagged_segments || 0} of ${scoreBasis.transcript_segments || 0} transcript segments flagged` : ''}. Transcript toxicity counts for Kids and Tweens only; unsupported age-policy checks still require review.`
      : 'No applicable video frames or transcript segments were available to score for this age group.';
  }

  if (safeGaugeChart) safeGaugeChart.destroy();
  if (unsafeGaugeChart) unsafeGaugeChart.destroy();

  safeGaugeChart = buildGauge(
    'safeGauge', hasScore ? safePct : 0, hasScore ? '#22c55e' : '#6b7280',
    hasScore ? '#dcfce7' : '#e5e7eb', hasScore
  );
  unsafeGaugeChart = buildGauge(
    'unsafeGauge', hasScore ? unsafePct : 0, hasScore ? '#ef4444' : '#6b7280',
    hasScore ? '#fee2e2' : '#e5e7eb', hasScore
  );
}

function buildGauge(canvasId, value, color, bgColor, hasScore) {
  const ctx = document.getElementById(canvasId)?.getContext('2d');
  if (!ctx) return null;
  return new Chart(ctx, {
    type: 'doughnut',
    data: {
      datasets: [{
        data: [value, 100 - value],
        backgroundColor: [color, bgColor],
        borderWidth: 0,
        circumference: 180,
        rotation: 270,
      }],
    },
    options: {
      responsive: false,
      cutout: '72%',
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false },
      },
    },
    plugins: [{
      id: 'gaugeText',
      afterDraw(chart) {
        const { ctx, width, height } = chart;
        ctx.save();
        ctx.font = 'bold 22px "Segoe UI", sans-serif';
        ctx.fillStyle = color;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const displayValue = Number.isInteger(value) ? value : value.toFixed(1);
        ctx.fillText(hasScore ? `${displayValue}%` : 'N/A', width / 2, height * 0.75);
        ctx.restore();
      },
    }],
  });
}

// ══ VERDICT ═══════════════════════════════════════════════════════════════════

function renderVerdict(verdict, complianceStatus, safeScore = null) {
  const banner = document.getElementById('verdictBanner');
  const icon   = document.getElementById('verdictIcon');
  const text   = document.getElementById('verdictText');
  if (!banner) return;

  banner.classList.remove('d-none');
  const isSafe = verdict === 'safe';
  const isIncomplete = complianceStatus === 'insufficient_evidence';
  banner.className = `verdict-banner mt-3 text-center ${
    isSafe ? 'verdict-safe' : isIncomplete ? 'verdict-incomplete' : 'verdict-review'
  }`;
  if (verdict === 'safe') {
    icon.className = 'fas fa-shield-halved fa-2x mb-2 text-success';
    text.textContent = '✓ Content Verified Safe — Ready to Publish';
    text.className = 'mb-0 text-success font-weight-bold';
  } else {
    icon.className = `fas ${isIncomplete ? 'fa-circle-exclamation text-warning' : 'fa-triangle-exclamation text-danger'} fa-2x mb-2`;
    text.textContent = isIncomplete
      ? Number.isFinite(safeScore) && safeScore >= PUBLISH_SCORE_OVERRIDE_THRESHOLD
        ? `No flags detected. A ${Math.round(safeScore)}% score qualifies for automatic publishing; age-tier compliance is not verified.`
        : `Age-tier compliance cannot be verified automatically. Publishing requires a ${PUBLISH_SCORE_OVERRIDE_THRESHOLD}% score.`
      : '✗ Content Violates the Selected Age-Tier Policy';
    text.className = `mb-0 ${isIncomplete ? 'text-warning' : 'text-danger'} font-weight-bold`;
  }
}

// ══ VIOLATIONS ════════════════════════════════════════════════════════════════

function renderViolations(violations) {
  const container = document.getElementById('violationsList');
  const remBtn    = document.getElementById('btnRemediate');
  if (!container) return;

  if (!violations || !violations.length) {
    container.innerHTML = '<p class="text-success text-center small mb-0"><i class="fas fa-check-circle mr-1"></i>No violations detected.</p>';
    if (remBtn) remBtn.style.display = 'none';
    return;
  }

  if (remBtn) {
    remBtn.style.display = violations.some(v => v.type === 'visual' || v.type === 'audio')
      ? 'inline-flex'
      : 'none';
  }

  container.innerHTML = violations.map(v => {
    const isVisual = v.type === 'visual';
    const isCategory = v.type === 'category';
    const ts = fmtSec(v.timestamp_sec);
    const action = isCategory
      ? ''
      : isVisual
      ? `<button class="btn btn-sm btn-outline-warning ml-2" onclick="applyBlur('${v.timestamp_sec}')"><i class="fas fa-eye-slash mr-1"></i>Auto-Blur</button>`
      : `<button class="btn btn-sm btn-outline-warning ml-2" onclick="applyMute('${v.timestamp_sec}', '${v.end_sec||''}')"><i class="fas fa-volume-mute mr-1"></i>Auto-Mute</button>`;
    return `
    <div class="violation-item">
      <div class="d-flex align-items-center">
        <span class="badge badge-${isVisual || isCategory ? 'danger' : 'warning'} mr-2">${isVisual ? 'Visual' : isCategory ? 'Category' : 'Audio'}</span>
        ${Number.isFinite(v.timestamp_sec) ? `<strong class="mr-2">@${ts}</strong>` : ''}
        <span>${escHtml(v.suggestion || v.description)}</span>
        ${action}
      </div>
    </div>`;
  }).join('');
}

// ══ CITATIONS ═════════════════════════════════════════════════════════════════

function renderCitations(citations) {
  const card = document.getElementById('citationsCard');
  const container = document.getElementById('citationsList');
  if (!container || !citations?.length) return;

  card?.classList.remove('d-none');
  container.innerHTML = citations.filter(c => c.violated).map(c => `
    <div class="citation-item">
      <span class="badge badge-spectra mr-2">${escHtml(c.id)}</span>
      <small>${escHtml(c.text)}</small>
    </div>
  `).join('') || '<p class="text-muted small">No policy clauses violated.</p>';
}

// ══ REMEDIATION ═══════════════════════════════════════════════════════════════

async function remediateVideo() {
  if (!currentVideoId) return;
  const btn = document.getElementById('btnRemediate');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Applying fixes…'; }

  try {
    const res = await fetch(`/admin/add-video/remediate/${currentVideoId}`, { method: 'POST' });
    const data = await res.json();
    showAlert(data.message || data.error || 'Done', data.ok ? 'success' : 'danger');
    if (data.ok) {
      showAlert('Remediation applied! Re-running AI analysis on fixed video…', 'info');
      await runAIAnalysis(currentVideoId);
    }
  } catch (err) {
    showAlert('Remediation error: ' + err.message, 'danger');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-magic mr-1"></i>Apply All AI Fixes'; }
  }
}

function applyBlur(ts) { remediateVideo(); }
function applyMute(ts, endTs) { remediateVideo(); }

// ══ PUBLISH GATE ══════════════════════════════════════════════════════════════

function updatePublishGate(result) {
  const btn = document.getElementById('btnPublish');
  const msg = document.getElementById('publishGateMsg');
  if (!btn) return;

  const eligible = result?.publish_eligible === true;
  const scoreOverride = result?.publish_score_override === true;
  btn.disabled = !eligible;
  btn.innerHTML = scoreOverride
    ? '<i class="fas fa-rocket mr-2"></i>Publish at 95%+ score'
    : '<i class="fas fa-rocket mr-2"></i>Publish Video';

  if (scoreOverride) {
    if (msg) {
      msg.textContent = `No detector flags were found. The ${PUBLISH_SCORE_OVERRIDE_THRESHOLD}% score threshold permits automatic publishing, but does not certify age-tier compliance.`;
      msg.className = 'text-warning small mt-2';
    }
  } else if (eligible) {
    if (msg) { msg.textContent = '✓ Content passed all safety checks. Ready to publish.'; msg.className = 'text-success small mt-2'; }
  } else {
    if (msg) {
      msg.textContent = analysisResult?.policy?.compliance_status === 'insufficient_evidence'
        ? `Age-tier compliance cannot be verified automatically. Publishing requires a ${PUBLISH_SCORE_OVERRIDE_THRESHOLD}% score.`
        : 'Publishing is locked: resolve the detected age-tier policy violations and run the analysis again.';
      msg.className = 'text-danger small mt-2';
    }
  }
}

async function publishVideo() {
  if (!currentVideoId) return;
  const btn = document.getElementById('btnPublish');
  document.getElementById('publishAlert')?.classList.add('d-none');
  btn.disabled = true;
  btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Publishing…';

  try {
    const res = await fetch(`/admin/add-video/publish/${currentVideoId}`, { method: 'POST' });
    const data = await res.json();
    const alertEl = document.getElementById('publishAlert');
    if (alertEl) {
      alertEl.className = `alert alert-${data.ok ? 'success' : 'danger'}`;
      alertEl.textContent = data.message || data.error;
      alertEl.classList.remove('d-none');
    }
    if (data.ok) {
      btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Preparing playback…';
      pollTranscodeStatus(currentVideoId);
    } else {
      updatePublishGate(analysisResult);
    }
  } catch (err) {
    showAlert('Publish error: ' + err.message, 'danger');
    updatePublishGate(analysisResult);
  }
}

async function pollTranscodeStatus(videoId) {
  const btn = document.getElementById('btnPublish');
  const MAX_POLLS = 60;
  let polls = 0;
  const interval = setInterval(async () => {
    polls++;
    try {
      const res = await fetch(`/admin/video-status/${videoId}`);
      if (!res.ok) throw new Error(`Status check failed (${res.status})`);
      const data = await res.json();
      if (data.status === 'published' && data.hls_ready) {
        clearInterval(interval);
        if (btn) btn.innerHTML = '<i class="fas fa-check-circle mr-2"></i>Published!';
        showAlert('Video is now live on the feed!', 'success');
        setTimeout(() => { window.location.href = '/feed'; }, 2000);
      } else if (data.publish_error) {
        clearInterval(interval);
        showPublishAlert(data.publish_error, 'danger');
        if (btn) updatePublishGate(analysisResult);
      } else if (polls >= MAX_POLLS) {
        clearInterval(interval);
        showAlert('Transcoding is taking longer than expected. Check the dashboard.', 'warning');
        if (btn) updatePublishGate(analysisResult);
      }
    } catch (err) {
      clearInterval(interval);
      showPublishAlert(`Unable to check publish status: ${err.message}`, 'warning');
      if (btn) updatePublishGate(analysisResult);
    }
  }, 1000);
}

function showPublishAlert(message, type) {
  const alertEl = document.getElementById('publishAlert');
  if (!alertEl) return;
  alertEl.className = `alert alert-${type} mb-3`;
  alertEl.textContent = message;
  alertEl.classList.remove('d-none');
}



// ══ UTILS ════════════════════════════════════════════════════════════════════

function showAlert(msg, type) {
  const el = document.getElementById('uploadAlert');
  if (!el) return;
  el.className = `alert alert-${type}`;
  el.textContent = msg;
  el.classList.remove('d-none');
}

function fmtSec(sec) {
  const s = Math.floor(sec || 0);
  return `${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toString().padStart(2,'0')}`;
}

function escHtml(s) {
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
