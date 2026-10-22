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
const ageCategoryPairs = [
  { age: ageSelect, category: categorySelect },
  { age: document.getElementById('vAgeSilo2'), category: document.getElementById('vCategory2') },
  { age: document.getElementById('vAgeSilo3'), category: document.getElementById('vCategory3') },
];

fileInput?.addEventListener('change', onFileSelected);

function updateCategories(age, category, resetSelection = false) {
  if (!age || !category) return;
  updateScoreAgeGroup();
  const selectedCategories = categoryOptions[age.value] || [];
  const previousCategory = category.value;
  category.replaceChildren(...selectedCategories.map((name) => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    return option;
  }));
  if (!resetSelection && selectedCategories.includes(previousCategory)) {
    category.value = previousCategory;
  }
  if (category !== categorySelect) {
    category.disabled = !age.value;
    if (!age.value) {
      category.replaceChildren(new Option('Select an age group first', ''));
    }
  }
}

function updateScoreAgeGroup() {
  const label = document.getElementById('scoreAgeGroup');
  if (label) {
    const selected = ageCategoryPairs
      .map(({ age }) => age?.value)
      .filter(Boolean);
    label.textContent = selected.length > 1
      ? `${selected.length} age groups`
      : AGE_GROUP_LABELS[selected[0]] || 'Select an age group';
  }
}

ageCategoryPairs.forEach(({ age, category }) => {
  if (age === ageSelect) {
    updateCategories(age, category);
  } else {
    category.disabled = !age.value;
  }
  age?.addEventListener('change', () => {
    updateCategories(age, category, true);
    updateScoreAgeGroup();
    validateAgeGroupSelections();
  });
});

function validateAgeGroupSelections() {
  const selectedGroups = ageCategoryPairs.map(({ age }) => age?.value).filter(Boolean);
  const hasDuplicates = new Set(selectedGroups).size !== selectedGroups.length;
  ageCategoryPairs.forEach(({ age }) => {
    age?.setCustomValidity(hasDuplicates && selectedGroups.filter((value) => value === age.value).length > 1
      ? 'Choose each age group only once.'
      : '');
  });
  return !hasDuplicates;
}

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
  const ageGroupTargets = ageCategoryPairs
    .filter(({ age, category }) => age?.value && category?.value)
    .map(({ age, category }) => ({
      age_silo: age.value,
      category: category.value,
    }));
  return {
    title: document.getElementById('vTitle')?.value?.trim() || '',
    description: document.getElementById('vDescription')?.value || '',
    age_silo: document.getElementById('vAgeSilo')?.value || 'kids',
    category: document.getElementById('vCategory')?.value || 'General',
    age_group_targets: ageGroupTargets,
  };
}

['vTitle', 'vDescription', 'vAgeSilo', 'vCategory', 'vAgeSilo2', 'vCategory2', 'vAgeSilo3', 'vCategory3'].forEach((id) => {
  document.getElementById(id)?.addEventListener('input', onVideoDetailsChanged);
  document.getElementById(id)?.addEventListener('change', onVideoDetailsChanged);
});

function onVideoDetailsChanged() {
  setAnalysisButton();
  if (!lastAnalyzedDetails) return;

  const details = getVideoDetails();
  const changed = Object.keys(lastAnalyzedDetails).some(
    (key) => JSON.stringify(details[key]) !== JSON.stringify(lastAnalyzedDetails[key])
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
  if (!validateAgeGroupSelections()) {
    return showAlert('Choose each target age group only once.', 'warning');
  }

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
  document.getElementById('uploadStatus').textContent = 'Uploading video...';
  document.getElementById('uploadPct').textContent = '0%';
  document.getElementById('uploadBar').style.width = '0%';

  try {
    const xhr = new XMLHttpRequest();
    const uploadStartedAt = performance.now();
    const uploadMetadata = new URLSearchParams({
      filename: file.name,
      title: details.title,
      description: details.description,
      age_silo: details.age_silo,
      category: details.category,
      age_group_targets: JSON.stringify(details.age_group_targets),
      duration_seconds: String(selectedDurationSeconds),
    });
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        document.getElementById('uploadBar').style.width = `${pct}%`;
        document.getElementById('uploadPct').textContent = `${pct}%`;
        if (pct === 100) {
          document.getElementById('uploadStatus').textContent = 'Upload sent; saving video...';
        } else {
          const elapsedSeconds = Math.max((performance.now() - uploadStartedAt) / 1000, 0.1);
          const bytesPerSecond = e.loaded / elapsedSeconds;
          const remainingSeconds = bytesPerSecond > 0
            ? Math.ceil((e.total - e.loaded) / bytesPerSecond)
            : null;
          const remainingText = remainingSeconds === null
            ? ''
            : ` · about ${Math.floor(remainingSeconds / 60)}m ${remainingSeconds % 60}s left`;
          document.getElementById('uploadStatus').textContent =
            `Uploading ${(e.loaded / 1024 / 1024).toFixed(1)} / ${(e.total / 1024 / 1024).toFixed(1)} MB` +
            ` · ${(bytesPerSecond / 1024 / 1024).toFixed(1)} MB/s${remainingText}`;
        }
      }
    };

    const uploadData = await new Promise((resolve, reject) => {
      xhr.open('POST', `${window.SPECTRA_STUDIO_URL}/add-video/upload?${uploadMetadata.toString()}`);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.onload = () => {
        let response;
        try {
          response = JSON.parse(xhr.responseText);
        } catch {
          reject(new Error(xhr.status === 413
            ? 'The video exceeds the server upload limit.'
            : `Upload failed (HTTP ${xhr.status}).`));
          return;
        }
        resolve(response);
      };
      xhr.onerror = () => reject(new Error('Network error while uploading the video.'));
      xhr.send(file);
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
    const res = await fetch(`${window.SPECTRA_STUDIO_URL}/add-video/analyze/${videoId}`, {
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
      : `Automated age-tier visual checks · ${v.detected_objects?.length || 0} object class(es)`);
    const transcriptError = analysisResult.transcript?.analysis_error;
    setStepStatus('audio', transcriptError ? 'fail' : 'done', transcriptError
      ? 'Audio transcription unavailable; run analysis again.'
      : `${reused ? 'Reused saved' : 'Whisper'} transcript · ${(analysisResult.transcript?.segments||[]).length} segments`);
    const a = analysisResult.audio;
    setStepStatus('text', 'done', `${reused ? 'Reused toxicity results' : 'BERT'} · ${a.violations?.length || 0} flagged segment(s)`);
    const checksIncomplete = analysisResult.policy.compliance_status === 'insufficient_evidence';
    setStepStatus(
      'rag',
      checksIncomplete ? 'fail' : 'done',
      checksIncomplete
        ? 'Automatic checks did not complete; run the analysis again.'
        : `Policy verdict: ${analysisResult.policy.verdict.toUpperCase()} · ${analysisResult.citations?.length || 0} clause(s) cited`
    );

    renderGauges(
      analysisResult.overall_safe_pct,
      analysisResult.overall_unsafe_pct,
      analysisResult.policy.score_basis
    );
    renderVerdict(
      analysisResult.verdict,
      analysisResult.policy.compliance_status,
      analysisResult.policy.age_group_results
    );
    renderAgeGroupResults(
      analysisResult.policy.age_group_results,
      analysisResult.permitted_age_silos || analysisResult.policy.permitted_age_silos || []
    );
    renderViolations(analysisResult.violations_summary);
    renderCitations(analysisResult.citations);
    updatePublishGate(analysisResult);

    showAlert(
      transcriptError
        ? 'Video analysis completed, but audio transcription failed. Run the analysis again.'
        : checksIncomplete
          ? 'Automatic age-tier checks did not complete. Run the analysis again.'
        : reused
          ? 'Details updated. Policy and metadata checks were refreshed using the saved video analysis.'
          : 'AI analysis complete.',
      transcriptError || checksIncomplete
        ? 'warning'
        : analysisResult.verdict === 'safe' ? 'success' : 'warning'
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
  document.getElementById('ageGroupResultsCard')?.classList.add('d-none');
  document.getElementById('ageGroupResults')?.replaceChildren();
  document.getElementById('permittedAgeGroups')?.replaceChildren();
  document.getElementById('violationsList').textContent = 'Analysis in progress…';
  document.getElementById('citationsCard').classList.add('d-none');
  document.getElementById('citationsList').replaceChildren();
  document.getElementById('btnRemediate').style.display = 'none';
  document.getElementById('btnPublish').disabled = true;
  document.getElementById('btnPublish').innerHTML = '<i class="fas fa-rocket mr-2"></i>Publish Video';
  document.getElementById('publishGateMsg').textContent = 'Complete AI analysis to unlock publishing.';
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
    const groupResults = analysisResult?.policy?.age_group_results || [];
    if (groupResults.length > 1) {
      scoreNote.textContent = `Automated checks for selected age groups: ${groupResults.map((group) => {
        const label = AGE_GROUP_LABELS[group.age_silo] || group.age_silo;
        const score = Number.isFinite(group.scan_score) ? `${group.scan_score}%` : 'N/A';
        const basis = group.score_basis || {};
        return `${label} (${group.category}): ${group.status} · ${score} (threshold ${group.score_threshold}%) · ${basis.flagged_frames || 0}/${basis.sampled_frames || 0} frames · ${basis.flagged_segments || 0}/${basis.transcript_segments || 0} transcript segments flagged`;
      }).join(' | ')}`;
    } else {
      const ageGroup = AGE_GROUP_LABELS[scoreBasis.age_silo] || 'selected age group';
      scoreNote.textContent = hasScore
        ? `Automated age-tier checks for ${ageGroup}: ${scoreBasis.flagged_frames || 0} of ${scoreBasis.sampled_frames || 0} sampled frames and ${scoreBasis.flagged_segments || 0} of ${scoreBasis.transcript_segments || 0} transcript segments flagged.`
        : 'No applicable video frames or transcript segments were available to score for this age group.';
    }
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

function renderVerdict(verdict, complianceStatus, groupResults = []) {
  const banner = document.getElementById('verdictBanner');
  const icon   = document.getElementById('verdictIcon');
  const text   = document.getElementById('verdictText');
  if (!banner) return;

  banner.classList.remove('d-none');
  const isSafe = verdict === 'safe';
  banner.className = `verdict-banner mt-3 text-center ${
    isSafe ? 'verdict-safe' : 'verdict-review'
  }`;
  if (verdict === 'safe') {
    icon.className = 'fas fa-shield-halved fa-2x mb-2 text-success';
    text.textContent = '✓ Content Approved for All Selected Age Groups';
    text.className = 'mb-0 text-success font-weight-bold';
  } else if (groupResults.some((group) => group.approved)) {
    const approvedGroups = groupResults
      .filter((group) => group.approved)
      .map((group) => AGE_GROUP_LABELS[group.age_silo] || group.age_silo)
      .join(', ');
    icon.className = 'fas fa-circle-exclamation fa-2x mb-2 text-warning';
    text.textContent = `Partial approval: permitted to stream in ${approvedGroups}.`;
    text.className = 'mb-0 text-warning font-weight-bold';
  } else {
    icon.className = 'fas fa-triangle-exclamation text-danger fa-2x mb-2';
    text.textContent = complianceStatus === 'insufficient_evidence'
      ? 'Automatic checks did not complete for the selected age groups. Run the analysis again.'
      : '✗ Content Violates the Selected Age-Tier Policy';
    text.className = 'mb-0 text-danger font-weight-bold';
  }
}

function renderAgeGroupResults(groupResults, permittedAgeSilos) {
  const card = document.getElementById('ageGroupResultsCard');
  const container = document.getElementById('ageGroupResults');
  const permittedNote = document.getElementById('permittedAgeGroups');
  if (!card || !container || !Array.isArray(groupResults)) return;

  container.innerHTML = groupResults.map((group) => {
    const label = AGE_GROUP_LABELS[group.age_silo] || group.age_silo;
    const approved = group.status === 'Approved';
    const statusClass = approved
      ? 'badge-success'
      : group.status === 'Review Required' ? 'badge-warning' : 'badge-danger';
    const score = Number.isFinite(group.scan_score)
      ? `${group.scan_score}%`
      : 'N/A';
    const policyThresholds = group.policy_thresholds || {};
    const activeRules = [
      policyThresholds.restricted_weapons
        ? 'Restricted weapon detections apply'
        : 'No age-specific weapon restriction',
      policyThresholds.transcript_toxicity
        ? 'Transcript toxicity check applies'
        : 'No age-specific transcript toxicity check',
      Number.isFinite(policyThresholds.max_audio_rms_dbfs)
        ? `Audio loudness limit ${policyThresholds.max_audio_rms_dbfs} dBFS RMS`
        : '',
    ].filter(Boolean).join(' · ');
    const suggestions = (group.remediation_suggestions || [])
      .map((suggestion) => `<li>${escHtml(suggestion)}</li>`)
      .join('');
    return `
      <section class="border-bottom border-secondary py-2">
        <div class="d-flex flex-wrap justify-content-between align-items-center">
          <div>
            <span class="badge badge-secondary mr-1">${escHtml(label)}</span>
            <span class="small text-muted">${escHtml(group.category || '')}</span>
          </div>
          <span class="badge ${statusClass}">${escHtml(group.status || 'Review Required')}</span>
        </div>
        <div class="small mt-1">Content scan score: <strong>${score}</strong> · minimum ${group.score_threshold}%</div>
        <div class="text-muted small">${escHtml(activeRules)}</div>
        <p class="small mb-1 mt-1">${escHtml(group.feedback || '')}</p>
        ${suggestions ? `<ul class="small mb-0 pl-4">${suggestions}</ul>` : ''}
      </section>`;
  }).join('');

  const approvedLabels = groupResults
    .filter((group) => permittedAgeSilos.includes(group.age_silo))
    .map((group) => AGE_GROUP_LABELS[group.age_silo] || group.age_silo);
  permittedNote.className = `small mb-0 mt-3 ${approvedLabels.length ? 'text-success' : 'text-warning'}`;
  permittedNote.textContent = approvedLabels.length
    ? `Permitted to stream in: ${approvedLabels.join(', ')}.`
    : 'No selected age group is currently approved for streaming.';
  card.classList.remove('d-none');
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
        : v.type === 'score'
        ? ''
        : `<button class="btn btn-sm btn-outline-warning ml-2" onclick="applyMute('${v.timestamp_sec}', '${v.end_sec||''}')"><i class="fas fa-volume-mute mr-1"></i>Auto-Mute</button>`;
    return `
    <div class="violation-item">
      <div class="d-flex align-items-center">
        <span class="badge badge-${isVisual || isCategory ? 'danger' : 'warning'} mr-2">${v.age_silo ? `${escHtml(AGE_GROUP_LABELS[v.age_silo] || v.age_silo)} · ` : ''}${isVisual ? 'Visual' : isCategory ? 'Category' : 'Audio'}</span>
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
    const res = await fetch(`${window.SPECTRA_STUDIO_URL}/add-video/remediate/${currentVideoId}`, { method: 'POST' });
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
  btn.disabled = !eligible;
  btn.innerHTML = '<i class="fas fa-rocket mr-2"></i>Publish Video';

  if (eligible) {
    const permitted = result?.permitted_age_silos || result?.policy?.permitted_age_silos || [];
    const labels = permitted.map((silo) => AGE_GROUP_LABELS[silo] || silo);
    if (msg) {
      msg.textContent = `✓ Eligible to publish in: ${labels.join(', ')}.`;
      msg.className = 'text-success small mt-2';
    }
  } else {
    if (msg) {
      msg.textContent = analysisResult?.policy?.compliance_status === 'insufficient_evidence'
        ? 'Automatic checks did not complete for an age group. Run the analysis again; only approved groups can stream the video.'
        : 'Publishing is locked because no selected age group passed moderation. Resolve the feedback and re-run analysis.';
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
    const res = await fetch(`${window.SPECTRA_STUDIO_URL}/add-video/publish/${currentVideoId}`, { method: 'POST' });
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
      const res = await fetch(`${window.SPECTRA_STUDIO_URL}/video-status/${videoId}`);
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
