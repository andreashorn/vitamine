const state = {
  sections: {},
  entries: [],
  publications: [],
  journalMetrics: [],
  importInbox: { items: [], counts: [] },
  identifiers: [],
  connections: {},
  cvImport: {},
  database: {},
  onboarding: null,
  pendingCvImportFiles: [],
  cloud: {
    enabled: false,
    workspace: null,
    plus: null,
    openAiProcessingConsent: null,
    activeJob: null,
    resuming: false,
  },
  orcidOauth: null,
  orcidOauthLoaded: false,
  zoteroOauth: null,
  orcidDiscoveryAttempted: false,
  zoteroCollections: [],
  zoteroLibraries: [],
  zoteroSetupOpened: false,
  exportProfiles: {
    short: { selected: [], candidates: [], settings: {} },
    ultrashort: { selected: [], candidates: [], settings: {} },
  },
  exportFormats: [],
  exportFormatsApiAvailable: true,
  exportSettings: {},
  exportArtifacts: {},
  exportPromptPlans: {},
  pendingCustomTemplateFile: null,
  biosketch: {
    contributions: [],
    publication_count: 0,
    contribution_limit: 5,
    products_per_contribution_limit: 4,
    publication_limit: 20,
  },
  selectedEntry: null,
  entryAutosave: {
    timer: null,
    pending: null,
    saving: false,
  },
  personAutosave: {
    timer: null,
    pending: null,
    saving: false,
  },
  institutionMappingPollActive: false,
  selectedPublicationId: null,
  suppressPublicationClick: false,
  publicationSort: { key: "year", direction: "desc" },
  publicationLoadSequence: 0,
  publicationCategoryFilters: new Set(["peer_reviewed", "patents"]),
  draggedPublicationId: null,
  draggedDropProfile: null,
  draggedDropId: null,
  draggedBiosketchContributionId: null,
  draggedBiosketchPublicationId: null,
  selectedBiosketchContributionId: null,
  collaborationMap: {
    data: null,
    mode: "collaborations",
    datasets: {},
    zoom: 2,
    origin: null,
    drag: null,
  },
  citationExplorer: {
    data: null,
    sort: "citations",
    firstLastOnly: false,
    selectedPublication: null,
    citingWorks: [],
    nextCitingPage: null,
  },
  citationNetwork: { data: null, graph: null, refreshing: false, settleTimer: null },
  activity: {
    timer: null,
    startedAt: null,
    depth: 0,
  },
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const PUBLICATION_CATEGORIES = [
  { key: "peer_reviewed", label: "Peer-reviewed papers" },
  { key: "patents", label: "Patents" },
  { key: "review", label: "Reviews" },
  { key: "books_chapters", label: "Books and chapters" },
  { key: "preprints", label: "Preprints" },
  { key: "manuscripts_in_preparation", label: "Manuscripts in preparation" },
  { key: "abstract", label: "Abstracts" },
  { key: "poster_presentations", label: "Poster presentations" },
  { key: "other", label: "Other" },
];

const DEFAULT_PUBLICATION_CATEGORIES = new Set(["peer_reviewed", "patents"]);
const OPENAI_MODEL_PRESETS = new Set(["gpt-5.4-nano", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"]);

function normalizedApiKeyInput(value) {
  let text = String(value || "").trim().replace(/\s+/g, "").replace(/^['"]|['"]$/g, "");
  if (text.toLowerCase().startsWith("bearer")) text = text.slice(6).trim();
  return text;
}

function looksLikeOpenAiApiKey(value) {
  return /^sk-[A-Za-z0-9_-]+$/.test(normalizedApiKeyInput(value));
}

function on(selector, eventName, handler, options) {
  const element = $(selector);
  if (element) element.addEventListener(eventName, handler, options);
  return element;
}

function appendConsole(text) {
  const output = $("#globalConsoleOutput");
  if (!output || !text) return;
  const stamp = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const current = output.textContent === "VitaMine is ready." ? "" : output.textContent;
  output.textContent = `${current}${current ? "\n\n" : ""}[${stamp}] ${text}`.trim();
  output.scrollTop = output.scrollHeight;
}

function setStatus(text, { log = true, error = false } = {}) {
  const status = $("#status");
  if (status) status.textContent = text;
  $("#globalActivity")?.classList.toggle("hasError", error);
  if (log) appendConsole(text);
}

function startProcessing(label, detail = "") {
  state.activity.depth += 1;
  if (state.activity.depth === 1) {
    state.activity.startedAt = Date.now();
    $("#globalActivity")?.classList.add("processing");
    const time = $("#processingTime");
    if (time) time.hidden = false;
    const update = () => {
      const elapsed = Math.max(0, Math.floor((Date.now() - state.activity.startedAt) / 1000));
      if (time) time.textContent = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
    };
    update();
    state.activity.timer = window.setInterval(update, 1000);
  }
  setStatus(label);
  if (detail) appendConsole(detail);
  let stopped = false;
  return (finalText = "") => {
    if (stopped) return;
    stopped = true;
    state.activity.depth = Math.max(0, state.activity.depth - 1);
    if (state.activity.depth === 0) {
      window.clearInterval(state.activity.timer);
      state.activity.timer = null;
      state.activity.startedAt = null;
      $("#globalActivity")?.classList.remove("processing");
      const time = $("#processingTime");
      if (time) time.hidden = true;
    }
    if (finalText) setStatus(finalText);
  };
}

async function api(path, options = {}) {
  const defaultHeaders = options.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: {
      ...defaultHeaders,
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.stderr || data.detail || "Request failed");
    error.status = response.status;
    throw error;
  }
  return data;
}

const delay = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

function createIdempotencyKey() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  return `vitamine-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

async function submitCloudJob(path, options = {}) {
  const idempotencyKey = createIdempotencyKey();
  const request = {
    ...options,
    headers: {
      ...(options.headers || {}),
      "Idempotency-Key": idempotencyKey,
    },
  };
  try {
    return await api(path, request);
  } catch (error) {
    if (error.status) throw error;
    await delay(300);
    return api(path, request);
  }
}

function accountInitials(account = {}) {
  const source = String(account.display_name || account.email || "VitaMine").trim();
  const words = source.split(/\s+/).filter(Boolean);
  if (words.length > 1) return `${words[0][0]}${words.at(-1)[0]}`.toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

function closeCloudAccountMenu() {
  const panel = $("#cloudAccountMenuPanel");
  const button = $("#cloudAccountMenuButton");
  if (panel) panel.hidden = true;
  button?.setAttribute("aria-expanded", "false");
}

async function returnToWorkspaceHome() {
  closeCloudAccountMenu();
  if (!state.cloud.enabled) {
    switchView("dashboard");
    $("#dashboardTab")?.focus();
    window.scrollTo({ top: 0, left: 0 });
    return;
  }

  setStatus(state.cloud.activeJob ? "The process will continue in the background." : "Saving your CV…");
  try {
    const response = await fetch("/gateway/workspace", {
      method: "DELETE",
      credentials: "same-origin",
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "Could not return to My CVs.");
    }
    window.location.assign("/");
  } catch (error) {
    setStatus(error.message, { error: true });
  }
}

function setCloudJobControls(running) {
  const download = $("#cloudDownloadDatabase");
  if (download) {
    download.classList.toggle("disabledLink", running);
    download.setAttribute("aria-disabled", String(running));
  }
}

function configureCloudWorkspace(workspace) {
  state.cloud.enabled = true;
  state.cloud.workspace = workspace;
  state.cloud.plus = workspace.plus || null;
  configureWorkspacePlus();
  if (state.exportFormats.length) renderExportFormats();
  const account = workspace.account || {};
  $("#cloudAccountMenu").hidden = false;
  $("#cloudAccountInitials").textContent = accountInitials(account);
  $("#cloudAccountName").textContent = account.display_name || "VitaMine account";
  $("#cloudAccountEmail").textContent = account.email || "";
  $("#cloudDownloadDatabase").setAttribute("download", workspace.filename || "workspace.vitamine");
  $("#brandHome").setAttribute("aria-label", "Back to My CVs");
  $("#brandHome").setAttribute("title", "My CVs");
  $("#cloudAccountMenuButton").addEventListener("click", () => {
    const panel = $("#cloudAccountMenuPanel");
    const opening = panel.hidden;
    panel.hidden = !opening;
    $("#cloudAccountMenuButton").setAttribute("aria-expanded", String(opening));
  });
  $("#cloudMyCvs").addEventListener("click", returnToWorkspaceHome);
  $("#cloudSignOut").addEventListener("click", async () => {
    closeCloudAccountMenu();
    try {
      await api("/api/account/logout", { method: "POST" });
    } finally {
      window.location.assign("/");
    }
  });
  $("#cloudDownloadDatabase").addEventListener("click", (event) => {
    if (!state.cloud.activeJob) return;
    event.preventDefault();
    closeCloudAccountMenu();
    setStatus("The backup will be available as soon as the background process finishes.");
  });
  document.addEventListener("click", (event) => {
    if (!$("#cloudAccountMenu")?.contains(event.target)) closeCloudAccountMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeCloudAccountMenu();
  });
}

function hasVitaminePlus() {
  return !state.cloud.enabled || Boolean(state.cloud.plus?.active);
}

function showWorkspacePlusDialog() {
  const plus = state.cloud.plus || {};
  const status = $("#workspacePlusStatus");
  if (status) status.textContent = plus.active
    ? (plus.plan === "trial" ? `Your VitaMine+ trial is active until ${new Date(plus.active_until).toLocaleDateString()}.` : "VitaMine+ is active for this account.")
    : "Upgrade to keep intelligent tools and full network details available.";
  $("#workspacePlusDialog")?.showModal();
}

function requirePlusUi() {
  if (hasVitaminePlus()) return true;
  showWorkspacePlusDialog();
  return false;
}

function requestOpenAiEnrichmentConsent() {
  const dialog = $("#openAiEnrichmentConsentDialog");
  const accept = $("#acceptOpenAiEnrichmentConsent");
  const cancel = $("#cancelOpenAiEnrichmentConsent");
  const cancelAction = $("#cancelOpenAiEnrichmentConsentAction");
  if (!dialog || !accept || !cancel || !cancelAction) return Promise.resolve(false);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (accepted) => {
      if (settled) return;
      settled = true;
      accept.removeEventListener("click", grantConsent);
      cancel.removeEventListener("click", declineConsent);
      cancelAction.removeEventListener("click", declineConsent);
      dialog.removeEventListener("close", closeDialog);
      if (dialog.open) dialog.close();
      resolve(accepted);
    };
    const declineConsent = () => finish(false);
    const closeDialog = () => finish(false);
    const grantConsent = async () => {
      accept.disabled = true;
      try {
        const payload = await api("/api/account/openai-processing-consent", {
          method: "PUT",
          body: JSON.stringify({ accepted: true }),
        });
        state.cloud.openAiProcessingConsent = payload.consent;
        finish(true);
      } catch (error) {
        setStatus(error.message, { error: true });
      } finally {
        accept.disabled = false;
      }
    };
    accept.addEventListener("click", grantConsent);
    cancel.addEventListener("click", declineConsent);
    cancelAction.addEventListener("click", declineConsent);
    dialog.addEventListener("close", closeDialog);
    dialog.showModal();
  });
}

async function ensureOpenAiEnrichmentConsent() {
  if (!state.cloud.enabled || !state.cloud.workspace?.background_jobs) return true;
  const account = await api("/api/account/databases");
  state.cloud.openAiProcessingConsent = account.openai_processing_consent || null;
  if (state.cloud.openAiProcessingConsent?.accepted) return true;
  return requestOpenAiEnrichmentConsent();
}

function configureWorkspacePlus() {
  const button = $("#workspacePlusButton");
  if (!button || !state.cloud.enabled) return;
  button.hidden = false;
  const developerToggle = Boolean(state.cloud.plus?.developer_toggle);
  button.classList.toggle("developer", developerToggle);
  button.classList.toggle("upgrade", !developerToggle && !hasVitaminePlus());
  button.setAttribute("aria-pressed", developerToggle ? String(hasVitaminePlus()) : "false");
  button.title = developerToggle
    ? "Temporary developer control: switch between VitaMine+ and the free plan"
    : "View VitaMine+ plan details";
  button.textContent = developerToggle
    ? (hasVitaminePlus() ? "DEV · Plus ON" : "DEV · Free mode")
    : (hasVitaminePlus() ? "VitaMine+" : "Upgrade to +");
}

async function toggleWorkspaceDeveloperPlus() {
  const button = $("#workspacePlusButton");
  if (!state.cloud.plus?.developer_toggle) {
    showWorkspacePlusDialog();
    return;
  }
  button.disabled = true;
  try {
    const payload = await api("/api/account/plus-developer-toggle", {
      method: "PUT",
      body: JSON.stringify({ active: !hasVitaminePlus() }),
    });
    state.cloud.plus = payload.plus;
    configureWorkspacePlus();
    if (state.exportFormats.length) renderExportFormats();
    await loadCollaborationMap();
    setStatus(hasVitaminePlus() ? "Developer mode: VitaMine+ enabled" : "Developer mode: free plan enabled");
  } catch (error) {
    setStatus(error.message, { error: true });
  } finally {
    button.disabled = false;
  }
}

function cloudJobMessage(job) {
  const progress = job?.progress || {};
  const percent = Number(progress.percent);
  const suffix = Number.isFinite(percent) && job.status === "running" ? ` · ${percent}%` : "";
  return `${progress.message || "VitaMine is working in the background"}${suffix}`;
}

function cloudJobCompletionMessage(kind) {
  return {
    cv_import: "CV import completed and saved",
    enrich_cv: "CV enrichment completed and saved",
    cleanup_cv: "CV cleanup suggestions are ready in the Inbox",
  }[kind] || "Background process completed";
}

async function waitForCloudJob(jobId) {
  let previousPhase = "";
  while (true) {
    const payload = await api(`/api/cloud/jobs/${encodeURIComponent(jobId)}`);
    const job = payload.job;
    state.cloud.activeJob = ["queued", "running"].includes(job.status) ? job : null;
    setCloudJobControls(Boolean(state.cloud.activeJob));
    const phase = String(job.progress?.phase || job.status);
    setStatus(cloudJobMessage(job), { log: phase !== previousPhase });
    previousPhase = phase;
    if (job.status === "succeeded") {
      await api(`/api/cloud/jobs/${encodeURIComponent(job.id)}/acknowledge`, { method: "POST" });
      state.cloud.activeJob = null;
      setCloudJobControls(false);
      return job.result || { ok: true };
    }
    if (job.status === "failed") {
      await api(`/api/cloud/jobs/${encodeURIComponent(job.id)}/acknowledge`, { method: "POST" }).catch(() => {});
      state.cloud.activeJob = null;
      setCloudJobControls(false);
      throw new Error(job.error || "The background process failed.");
    }
    await delay(2000);
  }
}

async function resumeCloudBackgroundJob() {
  if (!state.cloud.enabled || state.cloud.resuming) return;
  const payload = await api("/api/cloud/jobs");
  const databaseId = state.cloud.workspace?.database_id;
  const job = (payload.jobs || []).find((item) => item.database_id === databaseId);
  if (!job) return;
  if (job.status === "succeeded" || job.status === "failed") {
    if (job.status === "succeeded") {
      setStatus(cloudJobCompletionMessage(job.kind));
    } else {
      setStatus(job.error || "The background process failed.", { error: true });
    }
    await api(`/api/cloud/jobs/${encodeURIComponent(job.id)}/acknowledge`, { method: "POST" }).catch(() => {});
    return;
  }
  state.cloud.resuming = true;
  const stopProcessing = startProcessing(cloudJobMessage(job));
  setActionButtons(true);
  try {
    await waitForCloudJob(job.id);
    setStatus(cloudJobCompletionMessage(job.kind));
    window.location.reload();
  } catch (error) {
    setStatus(error.message, { error: true });
  } finally {
    stopProcessing();
    setActionButtons(false);
    state.cloud.resuming = false;
  }
}

function setActionButtons(disabled) {
  [
    "#enrichCvDashboard",
    "#cleanupCvDashboard",
    "#connectionsForm button[type='submit']",
    "#connectZotero",
    "#testZoteroConnection",
    "#loadZoteroCollections",
    "#useExampleDatabase",
    "#createBlankDatabase",
    "#loadDatabase",
    "#saveCvImportSettings",
    "#restoreInboxSelected",
    "#resolveInboxPublications",
    "#chooseCvImportFile",
    "#createPromptExportPlan",
    "#clearPromptExportPlan",
    "#importCvFile",
    "#buildUltraDashboard",
    "#buildShortDashboard",
    "#buildLongDashboard",
    "#buildBiosketchDashboard",
    "#newBiosketchContribution",
    "#deleteBiosketchContribution",
    "#importPublicationIds",
    "#resolvePublicationIdentifiers",
    "#acceptInboxSelected",
    "#rejectInboxSelected",
    "#acceptReviewSelected",
    "#rejectReviewSelected",
    "#selectInboxVisible",
    "#acceptHighConfidenceInbox",
    "#rejectDuplicateInbox",
  ].forEach((selector) => {
    const button = $(selector);
    if (button) button.disabled = disabled;
  });
  $$(".formatActionButton").forEach((button) => {
    button.disabled = disabled;
  });
  if (!disabled && $("#connectionZoteroSource")) updateZoteroSourceVisibility();
}

function fillSectionSelects() {
  const selectedFilter = $("#sectionFilter").value;
  const selectedEntrySection = $("#entrySection").value;
  const options = ['<option value="">All sections</option>']
    .concat(Object.entries(state.sections).map(([key, label]) => `<option value="${key}">${label}</option>`))
    .join("");
  $("#sectionFilter").innerHTML = options;
  $("#entrySection").innerHTML = Object.entries(state.sections)
    .map(([key, label]) => `<option value="${key}">${label}</option>`)
    .join("");
  if ([...$("#sectionFilter").options].some((option) => option.value === selectedFilter)) {
    $("#sectionFilter").value = selectedFilter;
  }
  if ([...$("#entrySection").options].some((option) => option.value === selectedEntrySection)) {
    $("#entrySection").value = selectedEntrySection;
  }
}

async function loadSummary() {
  const data = await api("/api/summary");
  state.sections = data.sections;
  fillSectionSelects();
  $("#summaryGrid").innerHTML = [
    summaryBox("Entries", data.entries.map((row) => `${state.sections[row.section_key] || row.section_key}: ${row.count}`)),
    summaryBox("Publications", data.publications.map((row) => `${row.source} / ${row.category}: ${row.count}`)),
    summaryBox("Import Inbox", [`Pending: ${data.import_inbox_pending || 0}`]),
    summaryBox("Warnings", data.warnings.map((row) => `${row.warning_type}: ${row.count}`)),
  ].join("");
  updateInboxBadge(data.import_inbox_pending || 0);
}

function updateInboxBadge(count) {
  const badge = $("#inboxBadge");
  if (!badge) return;
  badge.textContent = count;
  badge.hidden = !count;
}

function updateProfileSyncBadge(count) {
  const badge = $("#profileSyncBadge");
  if (!badge) return;
  badge.textContent = count;
  badge.hidden = !count;
}

function profileSyncProviderName(provider) {
  return provider === "zotero" ? "Zotero" : "ORCID";
}

function profileSyncIsWholeZoteroLibrary(provider, service) {
  return provider === "zotero" && String(service || "").endsWith(":library");
}

function profileSyncTitle(provider, wholeLibrary = false) {
  const target = provider === "zotero"
    ? (wholeLibrary ? "selected Zotero library" : "selected Zotero source")
    : "ORCID record";
  return `We’ve found current VitaMine publications that are not on your ${target}.`;
}

function profileSyncActionLabel(provider, wholeLibrary = false) {
  if (provider === "zotero") {
    return wholeLibrary ? "Add to selected Zotero library" : "Add to selected Zotero source";
  }
  return "Add to ORCID profile";
}

async function loadProfileSync() {
  const data = await api("/api/profile-sync/notifications");
  state.profileSync = data;
  updateProfileSyncBadge(data.total || 0);
  return data;
}

function profileSyncItemMarkup(item) {
  const publication = item.payload || {};
  const subtitle = [publication.year, publication.venue, publication.doi].filter(Boolean).join(" · ");
  return `
    <article class="inboxItem confidence-high" data-profile-sync-id="${item.id}">
      <label class="inboxCheck"><input type="checkbox" data-profile-sync-check="${item.id}" checked></label>
      <div class="inboxMain">
        <div class="inboxMeta"><span>Publication</span><span>${escapeHtml(profileSyncProviderName(item.provider))}</span></div>
        <strong>${escapeHtml(publication.title || "Publication")}</strong>
        ${subtitle ? `<small>${escapeHtml(subtitle)}</small>` : ""}
      </div>
    </article>`;
}

function selectedProfileSyncIds() {
  return $$("#profileSyncList [data-profile-sync-check]:checked")
    .map((input) => Number(input.dataset.profileSyncCheck)).filter(Boolean);
}

async function openProfileSync() {
  const data = await loadProfileSync();
  const candidates = (data.items || []).filter((item) => item.direction === "add_remote");
  const first = candidates[0];
  const items = first ? candidates.filter((item) => item.service === first.service) : [];
  if (!items.length) {
    setStatus("Your connected profiles are in sync.");
    return;
  }
  const direction = items[0].direction;
  const provider = items[0].provider || "orcid";
  state.profileSyncDirection = direction;
  state.profileSyncProvider = provider;
  state.profileSyncService = items[0].service;
  state.profileSyncWholeLibrary = profileSyncIsWholeZoteroLibrary(provider, state.profileSyncService);
  $("#profileSyncTitle").textContent = profileSyncTitle(provider, state.profileSyncWholeLibrary);
  const target = provider === "zotero"
    ? (state.profileSyncWholeLibrary ? "the selected Zotero library" : "the selected Zotero source")
    : "ORCID";
  $("#profileSyncDescription").textContent = `These DOI-backed papers are in the active VitaMine database and are selected so you can add them to ${target}.`;
  $("#applyProfileSync").textContent = profileSyncActionLabel(provider, state.profileSyncWholeLibrary);
  $("#profileSyncList").innerHTML = items.map(profileSyncItemMarkup).join("");
  $("#profileSyncDialog").showModal();
}

async function skipProfileSync() {
  const ids = selectedProfileSyncIds();
  if (!ids.length) return setStatus("Choose at least one publication.");
  const result = await api("/api/profile-sync/skip", { method: "POST", body: JSON.stringify({ service: state.profileSyncService, ids }) });
  setStatus(`Skipped ${result.skipped || 0} profile suggestion${result.skipped === 1 ? "" : "s"}.`);
  $("#profileSyncDialog").close();
  await loadProfileSync();
}

async function applyProfileSync() {
  const ids = selectedProfileSyncIds();
  if (!ids.length) return setStatus("Choose at least one publication.");
  if (!state.cloud.enabled) {
    setStatus(`Connect ${profileSyncProviderName(state.profileSyncProvider)} securely in hosted VitaMine before updating this profile.`);
    return;
  }
  const direction = state.profileSyncDirection;
  const provider = state.profileSyncProvider;
  setActionButtons(true);
  try {
    const result = await api(`/gateway/profile-sync/${provider}/actions`, {
      method: "POST", body: JSON.stringify({ direction, ids }),
    });
    setStatus(`${profileSyncActionLabel(provider, state.profileSyncWholeLibrary)}: ${result.completed || 0} publication${result.completed === 1 ? "" : "s"}.`);
    $("#profileSyncDialog").close();
    await Promise.all([loadProfileSync(), loadPublications()]);
  } catch (error) {
    if (provider === "orcid" && error.status === 403 && /Reconnect ORCID/.test(error.message || "")) {
      const authorization = await api("/gateway/orcid/oauth/start?write_access=true", { method: "POST" });
      window.location.assign(authorization.authorization_url);
      return;
    }
    if (provider === "zotero" && error.status === 403 && /Reconnect Zotero/.test(error.message || "")) {
      const authorization = await api("/gateway/zotero/oauth/start?write_access=true", { method: "POST" });
      window.location.assign(authorization.authorization_url);
      return;
    }
    throw error;
  } finally {
    setActionButtons(false);
  }
}

function inboxTypeLabel(type) {
  return {
    entry: "Entry",
    publication: "Publication",
    person: "Person",
    identifier: "Identifier",
    narrative_report: "Narrative",
    contribution: "Contribution",
    cleanup_suggestion: "Cleanup suggestion",
    metadata_update: "Metadata update",
  }[type] || type || "Candidate";
}

function confidenceClass(confidence) {
  return `confidence-${["low", "medium", "high"].includes(confidence) ? confidence : "medium"}`;
}

async function loadImportInbox(status = $("#inboxStatusFilter")?.value || "pending", targetType = $("#inboxTypeFilter")?.value || "all") {
  const data = await api(`/api/import-inbox?status=${encodeURIComponent(status)}&target_type=${encodeURIComponent(targetType)}`);
  state.importInbox = data;
  renderImportInbox("#inboxList", data.items || [], { selectableStatuses: ["pending", "rejected"] });
  updateInboxBadge((data.counts || [])
    .filter((row) => row.status === "pending")
    .reduce((total, row) => total + Number(row.count || 0), 0));
  return data;
}

function setVisibleInboxChecks(checked = true) {
  $$("#inboxList [data-inbox-check]:not(:disabled)").forEach((input) => {
    input.checked = checked;
  });
}

function renderImportInbox(selector, items, options = {}) {
  const container = $(selector);
  if (!container) return;
  container.innerHTML = items.length
    ? items.map((item) => inboxItemMarkup(item, options)).join("")
    : `<p class="emptyState">No import candidates here.</p>`;
}

function cleanupFieldLabel(field) {
  return {
    raw_text: "Source text",
    section_key: "Section",
  }[field] || String(field || "Field").replace(/_/g, " ");
}

function cleanupInlineDiff(value, replacement, changeClass) {
  const current = String(value || "").slice(0, 1800);
  const proposed = String(replacement || "").slice(0, 1800);
  let prefixEnd = 0;
  while (prefixEnd < current.length && prefixEnd < proposed.length && current[prefixEnd] === proposed[prefixEnd]) {
    prefixEnd += 1;
  }
  let suffixLength = 0;
  while (
    suffixLength < current.length - prefixEnd
    && suffixLength < proposed.length - prefixEnd
    && current[current.length - suffixLength - 1] === proposed[proposed.length - suffixLength - 1]
  ) {
    suffixLength += 1;
  }
  const source = changeClass === "cleanupChangedOld" ? current : proposed;
  const changed = source.slice(prefixEnd, suffixLength ? source.length - suffixLength : source.length);
  const before = source.slice(0, prefixEnd);
  const after = suffixLength ? source.slice(source.length - suffixLength) : "";
  return `${escapeHtml(before)}${changed ? `<span class="${changeClass}">${escapeHtml(changed)}</span>` : ""}${escapeHtml(after)}`;
}

function cleanupRecordColumnMarkup(record, label, suggestion = {}, side = "current") {
  const fields = Object.entries(record?.fields || {}).filter(([, value]) => String(value || "").trim());
  if (!record || !fields.length) return "";
  const changedField = suggestion.operation === "edit"
    && suggestion.record_type === record.record_type
    && String(suggestion.record_id) === String(record.record_id)
    ? suggestion.field : "";
  return `
    <section class="cleanupPreviewColumn">
      <h4>${escapeHtml(label)} · ${escapeHtml(record.record_type)} #${escapeHtml(record.record_id)}</h4>
      <dl>${fields.map(([field, value]) => {
        const changed = field === changedField;
        const text = changed
          ? cleanupInlineDiff(value, suggestion.new_text, side === "current" ? "cleanupChangedOld" : "cleanupChangedNew")
          : escapeHtml(String(value)).slice(0, 1800);
        return `<div><dt>${escapeHtml(cleanupFieldLabel(field))}</dt><dd>${text}</dd></div>`;
      }).join("")}</dl>
    </section>
  `;
}

function cleanupRemovalColumnMarkup() {
  return `
    <section class="cleanupPreviewColumn cleanupRemovalPreview">
      <h4>Proposed action</h4>
      <p>Remove this record.</p>
    </section>
  `;
}

function cleanupComparisonMarkup(left, right) {
  return `<div class="cleanupPreviewComparison">${left}<span class="cleanupComparisonArrow" aria-hidden="true">→</span>${right}</div>`;
}

function cleanupPreviewMarkup(item) {
  if (item.target_type !== "cleanup_suggestion") return "";
  const suggestion = item.payload?.cleanup_csv || {};
  const currentRecord = item.payload?.record_preview;
  const relatedRecord = item.payload?.related_record_preview;
  const operation = suggestion.operation;
  const current = cleanupRecordColumnMarkup(
    currentRecord,
    operation === "merge" ? "Duplicate record" : "Current record",
    suggestion,
    "current",
  );
  let proposed = "";
  if (operation === "edit") {
    proposed = cleanupRecordColumnMarkup(currentRecord, "Proposed record", suggestion, "proposed");
  } else if (operation === "merge") {
    proposed = cleanupRecordColumnMarkup(relatedRecord, "Record to keep", suggestion, "proposed");
  } else if (operation === "delete") {
    proposed = cleanupRemovalColumnMarkup();
  }
  if (!current && !proposed) return "";
  const content = current && proposed
    ? cleanupComparisonMarkup(current, proposed)
    : current || proposed;
  return `<details class="cleanupPreview" open><summary>Preview suggested change</summary>${content}</details>`;
}

function inboxItemMarkup(item, options = {}) {
  const selectableStatuses = options.selectableStatuses || ["pending"];
  const selectable = selectableStatuses.includes(item.status || "pending");
  const duplicate = item.duplicate_of_id ? `<span class="duplicateBadge">Possible duplicate</span>` : "";
  const cleanup = item.target_type === "cleanup_suggestion";
  const cautiousHonor = item.target_type === "entry" && item.payload?.section_key === "honors";
  const identityReview = Boolean(item.payload?._identity_review_required);
  const manualReview = cautiousHonor || identityReview
    ? `<span class="duplicateBadge">${identityReview ? "Identity uncertain" : "Review manually"}</span>`
    : "";
  const checked = selectable && (cleanup || (!item.duplicate_of_id && !cautiousHonor && !identityReview && item.confidence !== "low")) ? "checked" : "";
  const disabled = selectable ? "" : "disabled";
  const raw = item.raw_text || item.payload?.raw_citation || item.payload?.raw_text || "";
  const rawPreviewLimit = cleanup ? 360 : 900;
  return `
    <article class="inboxItem ${confidenceClass(item.confidence)}" data-inbox-id="${item.id}">
      <label class="inboxCheck">
        <input type="checkbox" data-inbox-check="${item.id}" ${checked} ${disabled}>
      </label>
      <div class="inboxMain">
        <div class="inboxMeta">
          <span>${escapeHtml(inboxTypeLabel(item.target_type))}</span>
          <span>${escapeHtml(item.confidence || "medium")}</span>
          ${duplicate}
          ${manualReview}
          ${item.document_title ? `<span>${escapeHtml(item.document_title)}</span>` : ""}
        </div>
        <strong>${escapeHtml(item.title || inboxTypeLabel(item.target_type))}</strong>
        ${item.subtitle ? `<small>${escapeHtml(item.subtitle)}</small>` : ""}
        ${identityReview ? `<small>${escapeHtml(item.payload?._identity_review_reason || "The registries could not distinguish this author from a namesake.")}</small>` : ""}
        ${cleanup ? `<small>${escapeHtml(item.payload?.cleanup_csv?.rationale || "Review the proposed change before applying it.")}</small>` : ""}
        ${raw ? `<p>${escapeHtml(raw).slice(0, rawPreviewLimit)}</p>` : ""}
        ${cleanupPreviewMarkup(item)}
      </div>
    </article>
  `;
}

function selectedInboxIds(containerSelector) {
  return $$(`${containerSelector} [data-inbox-check]:checked`).map((input) => Number(input.dataset.inboxCheck)).filter(Boolean);
}

async function acceptInboxItems(containerSelector = "#inboxList") {
  const ids = selectedInboxIds(containerSelector);
  if (!ids.length) {
    setStatus("Choose at least one import candidate.");
    return;
  }
  const data = await api("/api/import-inbox/accept", {
    method: "POST",
    body: JSON.stringify({ ids }),
  });
  setStatus(`Accepted ${data.accepted || 0}; skipped ${Number(data.duplicates || 0) + Number(data.skipped || 0)}.`);
  await refreshAfterInboxReview();
}

async function rejectInboxItems(containerSelector = "#inboxList") {
  const ids = selectedInboxIds(containerSelector);
  if (!ids.length) {
    setStatus("Choose at least one import candidate.");
    return;
  }
  const data = await api("/api/import-inbox/reject", {
    method: "POST",
    body: JSON.stringify({ ids }),
  });
  setStatus(`Rejected ${data.rejected || 0} import candidate${data.rejected === 1 ? "" : "s"}.`);
  await refreshAfterInboxReview();
}

async function restoreInboxItems(containerSelector = "#inboxList") {
  const ids = selectedInboxIds(containerSelector);
  if (!ids.length) {
    setStatus("Choose at least one rejected import candidate.");
    return;
  }
  const data = await api("/api/import-inbox/restore", {
    method: "POST",
    body: JSON.stringify({ ids }),
  });
  setStatus(`Restored ${data.restored || 0} import candidate${data.restored === 1 ? "" : "s"} to pending.`);
  await refreshAfterInboxReview();
}

async function resolveInboxPublications(containerSelector = "#inboxList") {
  const ids = selectedInboxIds(containerSelector);
  if (!ids.length) {
    setStatus("Choose at least one publication candidate.");
    return;
  }
  setActionButtons(true);
  try {
    const data = await api("/api/import-inbox/resolve-publications", {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    setStatus(`Resolved ${data.resolved || 0} publication candidate${data.resolved === 1 ? "" : "s"}; unresolved ${data.unresolved || 0}.`);
    await refreshAfterInboxReview();
  } finally {
    setActionButtons(false);
  }
}

async function acceptHighConfidenceInbox() {
  const data = await api("/api/import-inbox/accept-high-confidence", { method: "POST" });
  setStatus(`Accepted ${data.accepted || 0} high-confidence candidate${data.accepted === 1 ? "" : "s"}.`);
  await refreshAfterInboxReview();
}

async function rejectDuplicateInbox() {
  const data = await api("/api/import-inbox/reject-duplicates", { method: "POST" });
  setStatus(`Rejected ${data.rejected || 0} duplicate-looking candidate${data.rejected === 1 ? "" : "s"}.`);
  await refreshAfterInboxReview();
}

async function refreshAfterInboxReview() {
  await loadSummary();
  await loadImportInbox();
  await loadMetrics();
  await loadEntries();
  await loadPublications();
  await loadPerson();
  await loadNarrativeReport();
  await loadBiosketch();
  const dialog = $("#importReviewDialog");
  if (dialog?.open) {
    const data = await loadImportInbox("pending");
    renderImportInbox("#importReviewList", data.items || [], { editable: true });
    if (!data.items?.length) dialog.close();
  }
}

async function openImportReview(data = null) {
  const inbox = await loadImportInbox("pending");
  const dialog = $("#importReviewDialog");
  if (!dialog) return;
  const staged = data?.staged || {};
  $("#importReviewSummary").innerHTML = [
    `<strong>${data?.candidates_staged ?? inbox.items.length} candidates staged</strong>`,
    `<span>Entries ${staged.entries || 0}</span>`,
    `<span>Publications ${staged.publications || 0}</span>`,
    `<span>Person ${staged.person || 0}</span>`,
    `<span>Narrative ${staged.narrative || 0}</span>`,
    `<span>Contributions ${staged.contributions || 0}</span>`,
  ].join("");
  renderImportInbox("#importReviewList", inbox.items || [], { editable: true });
  if (typeof dialog.showModal === "function") dialog.showModal();
}

async function loadDatabaseInfo() {
  const data = await api("/api/database");
  state.database = data;
  const label = data.is_example ? `${data.active_name} (example)` : data.active_name;
  $("#databaseName").textContent = label;
  $("#databaseName").title = data.active || "";
  $("#renameDatabase").disabled = !!data.is_example;
}

function setRenameDatabaseError(message = "") {
  const error = $("#renameDatabaseError");
  const field = $("#renameDatabaseNameField");
  if (!error || !field) return;
  error.textContent = message;
  error.hidden = !message;
  field.classList.toggle("invalid", !!message);
}

function openRenameDatabaseDialog() {
  const dialog = $("#renameDatabaseDialog");
  const input = $("#renameDatabaseName");
  if (!dialog || !input) return;
  input.value = (state.database.active_name || "default.vitamine").replace(/\.vitamine$/i, "");
  setRenameDatabaseError("");
  dialog.showModal();
  input.focus();
  input.select();
}

function closeRenameDatabaseDialog() {
  $("#renameDatabaseDialog")?.close();
}

async function renameDatabase(event) {
  event.preventDefault();
  const name = $("#renameDatabaseName")?.value.trim() || "";
  if (!name) {
    setRenameDatabaseError("Enter a database name.");
    return;
  }
  try {
    await api("/api/database/rename", { method: "POST", body: JSON.stringify({ name }) });
    closeRenameDatabaseDialog();
    setStatus("Database renamed");
    window.location.reload();
  } catch (error) {
    setRenameDatabaseError(error.message || "Could not rename database.");
  }
}

const ONBOARDING_STEPS = {
  import_cv: {
    target: "#importCvDropzone",
    title: "Start with your CV",
    text: "Drop one or several CV files here.",
    image: "/static/assets/onboarding_arrow.png",
    view: "dashboard",
  },
  orcid: {
    target: "#linkOrcid",
    title: "Link your ORCID next",
    text: "We’ll try a confident match from your imported CV.",
    image: "/static/assets/onboarding_orcid.png?v=2",
    direction: "down-left",
    compact: true,
    view: "dashboard",
  },
  zotero: {
    target: "#connectZotero",
    title: "Add your Zotero library",
    text: "Open Zotero’s key page in a new tab. You’ll return here to paste the key.",
    image: "/static/assets/onboarding_arrow_blank.png",
    direction: "down-left",
    compact: true,
    view: "dashboard",
  },
  enrich: {
    target: "#enrichCvDashboard",
    title: "Find what’s new",
    text: "Enrich your CV from trusted databases and reviewed online sources.",
    image: "/static/assets/onboarding_enrich.png?v=2",
    direction: "down-left",
    compact: true,
    view: "dashboard",
  },
  inbox: {
    target: "#inboxTab",
    title: "You’ve got mail!",
    text: "We found additional assets to populate your CV. Review them in the Inbox.",
    image: "/static/assets/onboarding_inbox.png",
    direction: "up-right",
    view: "dashboard",
  },
};

function clearOnboardingTarget() {
  $$(".onboardingTarget").forEach((element) => element.classList.remove("onboardingTarget"));
}

function positionOnboardingCoach(target, config = {}) {
  const coach = $("#onboardingCoach");
  if (!coach || !target) return;
  const rect = target.getBoundingClientRect();
  if (config.compact) {
    const coachRect = coach.getBoundingClientRect();
    const gap = 18;
    let left = rect.left;
    let top = rect.top - coachRect.height - gap;
    if (top < 8) top = rect.bottom + gap;
    left = Math.max(8, Math.min(left, window.innerWidth - coachRect.width - 8));
    top = Math.max(8, Math.min(top, window.innerHeight - coachRect.height - 8));
    coach.style.left = `${left}px`;
    coach.style.top = `${top}px`;
    coach.classList.remove("flipX");
    return;
  }
  const width = Math.min(580, window.innerWidth * 0.56);
  const image = $("#onboardingCoachImage");
  const ratio = image?.naturalWidth ? image.naturalHeight / image.naturalWidth : 998 / 1576;
  const height = width * ratio;
  if (config.direction === "up-right") {
    // Stop the tip below-left of the control instead of covering it.
    const tipX = rect.left - 18;
    const tipY = rect.bottom + 24;
    let left = tipX - width * 0.91;
    let top = tipY - height * 0.1;
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    top = Math.max(8, Math.min(top, window.innerHeight - height - 8));
    coach.style.left = `${left}px`;
    coach.style.top = `${top}px`;
    coach.classList.remove("flipX");
    return;
  }
  if (config.direction === "down-left") {
    // Leave enough air around the control for the entire oversized
    // arrowhead—not merely its mathematical tip—to remain outside it.
    const tipX = rect.right + 34;
    const tipY = rect.top - 70;
    let left = tipX - width * 0.1;
    let top = tipY - height * 0.84;
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    top = Math.max(8, Math.min(top, window.innerHeight - height - 8));
    coach.style.left = `${left}px`;
    coach.style.top = `${top}px`;
    coach.classList.remove("flipX");
    return;
  }
  let left = rect.left + rect.width / 2 - width * 0.08;
  let top = rect.top - height * 0.86;
  let flip = false;
  if (left + width > window.innerWidth - 10) {
    flip = true;
    left = rect.left + rect.width / 2 - width * 0.92;
  }
  left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
  top = Math.max(8, Math.min(top, window.innerHeight - height - 8));
  coach.style.left = `${left}px`;
  coach.style.top = `${top}px`;
  coach.classList.toggle("flipX", flip);
}

function renderOnboardingCoach() {
  const coach = $("#onboardingCoach");
  clearOnboardingTarget();
  if (state.pendingCvImportFiles.length || $("#llmOnboardingDialog")?.open || $("#llmAdvancedDialog")?.open) {
    if (coach) coach.hidden = true;
    return;
  }
  if (!coach || !state.onboarding?.step) {
    if (coach) coach.hidden = true;
    return;
  }
  let config = ONBOARDING_STEPS[state.onboarding.step];
  if (state.onboarding.step === "zotero" && state.zoteroSetupOpened) {
    config = {
      ...config,
      target: "#connectionZoteroKey",
      title: "Paste your Zotero key",
      text: "Return from Zotero, paste the new key here, then choose Save Connections.",
    };
  }
  const target = config ? $(config.target) : null;
  if (!config || !target) {
    coach.hidden = true;
    return;
  }
  if (config.view) switchView(config.view);
  target.scrollIntoView({ block: "center", behavior: "smooth" });
  target.classList.add("onboardingTarget");
  $("#onboardingCoachImage").src = config.image;
  $("#onboardingCoachTitle").textContent = config.title;
  $("#onboardingCoachText").textContent = config.text;
  coach.classList.toggle("importStep", state.onboarding.step === "import_cv");
  coach.classList.toggle("fixedStep", !!config.direction);
  coach.classList.toggle("downLeftStep", config.direction === "down-left");
  coach.classList.toggle("compactStep", !!config.compact);
  coach.hidden = false;
  const position = () => positionOnboardingCoach(target, config);
  $("#onboardingCoachImage").onload = position;
  window.setTimeout(position, 350);
}

function showLlmOnboardingDialog() {
  if (usesManagedLlm()) {
    closeManagedLlmOnboarding();
    return;
  }
  const dialog = $("#llmOnboardingDialog");
  if (dialog && !dialog.open) {
    dialog.showModal();
    updateSimpleOnboardingActions();
  }
}

function usesManagedLlm() {
  return state.cvImport?.managed === true
    || state.cvImport?.configuration_allowed === false
    || state.onboarding?.llm_managed === true
    || state.onboarding?.skip_llm_configuration === true;
}

function closeManagedLlmOnboarding() {
  $("#llmOnboardingDialog")?.close();
  $("#llmAdvancedDialog")?.close();
  const error = $("#llmOnboardingError");
  if (error) {
    error.textContent = "";
    error.hidden = true;
  }
  setStatus("VitaMine cloud uses its managed AI service. No personal OpenAI API key or local model is needed.");
}

function updateSimpleOnboardingActions() {
  const key = $("#onboardingApiKey")?.value.trim() || "";
  const openAiButton = $("#saveOnboardingApiKey");
  const localButton = $("#useLocalOnboardingModel");
  const canUseOpenAi = looksLikeOpenAiApiKey(key);
  openAiButton.disabled = !canUseOpenAi;
  openAiButton.classList.toggle("onboardingPrimaryAction", canUseOpenAi);
  localButton.classList.toggle("onboardingPrimaryAction", !canUseOpenAi);
}

function showAdvancedLlmDialog() {
  if (usesManagedLlm()) {
    closeManagedLlmOnboarding();
    return;
  }
  $("#llmOnboardingDialog")?.close();
  $("#onboardingCoach").hidden = true;
  clearOnboardingTarget();
  updateOnboardingProviderVisibility();
  $("#llmAdvancedStatus").textContent = "";
  $("#llmAdvancedDialog")?.showModal();
}

function closeAdvancedLlmDialog() {
  $("#llmAdvancedDialog")?.close();
  showLlmOnboardingDialog();
  updateSimpleOnboardingActions();
}

async function loadOnboarding() {
  state.onboarding = await api("/api/onboarding");
  if (
    state.onboarding.step === "orcid"
    && state.onboarding.population?.has_person
    && !state.orcidDiscoveryAttempted
  ) {
    state.orcidDiscoveryAttempted = true;
    try {
      const discovery = await api("/api/orcid/discover", { method: "POST" });
      if (discovery.auto_linked) {
        await loadConnections();
        await loadPersonIdentifiers();
        state.onboarding = await api("/api/onboarding");
      }
    } catch (error) {
      console.warn("Automatic ORCID discovery failed", error);
    }
  }
  renderOnboardingCoach();
  return state.onboarding;
}

async function skipOnboardingStep() {
  const step = state.onboarding?.step;
  if (!step) return;
  state.onboarding = await api("/api/onboarding/step", {
    method: "POST",
    body: JSON.stringify({ step, action: "skip" }),
  });
  state.zoteroSetupOpened = false;
  renderOnboardingCoach();
}

async function completeOnboarding() {
  const step = state.onboarding?.step || "inbox";
  state.onboarding = await api("/api/onboarding/step", {
    method: "POST",
    body: JSON.stringify({ step, action: "complete" }),
  });
  renderOnboardingCoach();
}

async function configureOnboardingLlm(provider, apiKey = "", overrides = {}) {
  const payload = {
    provider,
    ollama_url: "http://127.0.0.1:11434",
    ollama_model: "llama3.1:8b",
    api_base_url: "https://api.openai.com/v1",
    api_model: "gpt-4.1-mini",
    bundled_llama_model_path: "",
    bundled_llama_ctx_size: "4096",
    api_key: apiKey,
    ...overrides,
  };
  try {
    await api("/api/cv-import/settings", { method: "PUT", body: JSON.stringify(payload) });
  } catch (error) {
    if (error.status !== 403) throw error;
    try {
      await refreshLlmImportPolicy();
    } catch {
      throw error;
    }
    if (!usesManagedLlm()) throw error;
    closeManagedLlmOnboarding();
    const pendingFiles = state.pendingCvImportFiles;
    state.pendingCvImportFiles = [];
    if (pendingFiles.length) await importCvFiles(pendingFiles);
    return;
  }
  $("#llmOnboardingDialog")?.close();
  $("#llmAdvancedDialog")?.close();
  await loadCvImportSettings();
  await loadOnboarding();
  const pendingFiles = state.pendingCvImportFiles;
  state.pendingCvImportFiles = [];
  if (pendingFiles.length) await importCvFiles(pendingFiles);
}

async function saveOnboardingApiKey(event) {
  event.preventDefault();
  const key = $("#onboardingApiKey")?.value.trim() || "";
  const error = $("#llmOnboardingError");
  if (!key) {
    error.textContent = "Paste an OpenAI API key or choose the local model.";
    error.hidden = false;
    return;
  }
  try {
    error.hidden = true;
    await configureOnboardingLlm("openai", key);
  } catch (caught) {
    error.textContent = caught.message;
    error.hidden = false;
  }
}

async function useLocalOnboardingModel() {
  if (usesManagedLlm()) {
    closeManagedLlmOnboarding();
    return;
  }
  const error = $("#llmOnboardingError");
  try {
    await configureOnboardingLlm("bundled_llama");
  } catch (caught) {
    if (error) {
      error.textContent = caught.message;
      error.hidden = false;
    }
  }
}

function updateOnboardingProviderVisibility() {
  const provider = $("#onboardingProvider")?.value || "bundled_llama";
  $("#onboardingOllamaFields").hidden = provider !== "ollama";
  $("#onboardingApiFields").hidden = !["openai", "openai_compatible"].includes(provider);
  $("#onboardingApiBaseUrlField").hidden = provider === "openai";
  if (provider === "openai") $("#onboardingApiBaseUrl").value = "https://api.openai.com/v1";
}

function onboardingAdvancedPayload() {
  return {
    provider: $("#onboardingProvider").value,
    ollama_url: $("#onboardingOllamaUrl").value.trim(),
    ollama_model: $("#onboardingOllamaModel").value.trim(),
    api_base_url: $("#onboardingApiBaseUrl").value.trim(),
    api_model: $("#onboardingApiModel").value.trim(),
    api_key: $("#onboardingAdvancedApiKey").value.trim(),
  };
}

async function testOnboardingAdvancedConfig() {
  const status = $("#llmAdvancedStatus");
  status.textContent = "Testing connection…";
  try {
    const result = await api("/api/cv-import/test-connection", {
      method: "POST",
      body: JSON.stringify(onboardingAdvancedPayload()),
    });
    status.textContent = result.message || "Connection successful.";
  } catch (caught) {
    status.textContent = caught.message;
  }
}

async function saveOnboardingAdvancedConfig(event) {
  event.preventDefault();
  const settings = onboardingAdvancedPayload();
  const status = $("#llmAdvancedStatus");
  try {
    status.textContent = "";
    await configureOnboardingLlm(settings.provider, settings.api_key, settings);
  } catch (caught) {
    status.textContent = caught.message;
  }
}

function openOrcidLinkDialog() {
  $("#orcidLinkValue").value = $("#connectionOrcid").value || "";
  $("#orcidDiscoveryResults").innerHTML = "";
  $("#orcidLinkError").hidden = true;
  renderOrcidOAuthStatus();
  $("#orcidLinkDialog").showModal();
}

function renderOrcidOAuthStatus() {
  const panel = $("#orcidOauthPanel");
  const fallback = $("#orcidManualFallback");
  const button = $("#connectOrcidOAuth");
  const label = $("#connectOrcidOAuthLabel");
  const description = $("#orcidOauthDescription");
  const status = $("#orcidOauthStatus");
  const oauth = state.orcidOauth;
  const currentOrcid = String(state.connections?.orcid_id || "").toUpperCase();
  const available = Boolean(state.cloud.enabled && oauth?.configured);
  panel.hidden = !state.cloud.enabled;
  fallback.open = !available;
  if (!state.cloud.enabled) return;
  if (!state.orcidOauthLoaded) {
    description.textContent = "Checking whether secure ORCID sign-in is available…";
    label.textContent = "Connect your ORCID iD";
    status.textContent = "";
    button.disabled = true;
    return;
  }
  if (!available) {
    description.textContent = "Secure ORCID sign-in has not yet been enabled for this VitaMine deployment.";
    label.textContent = "ORCID sign-in unavailable";
    status.textContent = "You can link an iD manually below in the meantime.";
    button.disabled = true;
    return;
  }
  description.textContent = "Sign in at ORCID to verify your iD and return securely to VitaMine.";
  button.disabled = false;
  if (!oauth.connected) {
    label.textContent = "Connect your ORCID iD";
    status.textContent = "";
    return;
  }
  const identity = [oauth.display_name, oauth.orcid_id].filter(Boolean).join(" · ");
  status.textContent = `Authenticated with ORCID${identity ? ` as ${identity}` : ""}.`;
  label.textContent = currentOrcid === String(oauth.orcid_id || "").toUpperCase()
    ? "Reconnect your ORCID iD"
    : "Use this ORCID iD for this CV";
  button.disabled = false;
}

async function loadOrcidOAuthStatus() {
  if (!state.cloud.enabled) {
    state.orcidOauth = null;
    state.orcidOauthLoaded = true;
    renderOrcidOAuthStatus();
    return;
  }
  try {
    state.orcidOauth = await api("/gateway/orcid/oauth/status");
  } catch (error) {
    state.orcidOauth = null;
    console.warn("ORCID OAuth status is unavailable:", error);
  } finally {
    state.orcidOauthLoaded = true;
  }
  renderOrcidOAuthStatus();
}

async function connectOrcidOAuth() {
  const oauth = state.orcidOauth;
  const currentOrcid = String(state.connections?.orcid_id || "").toUpperCase();
  if (oauth?.connected && currentOrcid !== String(oauth.orcid_id || "").toUpperCase()) {
    const button = $("#connectOrcidOAuth");
    button.disabled = true;
    try {
      const result = await api("/gateway/orcid/oauth/link-current", { method: "POST" });
      $("#connectionOrcid").value = result.orcid_id;
      $("#orcidLinkValue").value = result.orcid_id;
      await loadConnections();
      await loadPersonIdentifiers();
      await loadOnboarding();
      pollAutomaticInstitutionMapping();
      setStatus("Authenticated ORCID iD linked");
    } catch (error) {
      $("#orcidLinkError").textContent = error.message;
      $("#orcidLinkError").hidden = false;
    } finally {
      button.disabled = false;
    }
    return;
  }
  try {
    const started = await api("/gateway/orcid/oauth/start", { method: "POST" });
    window.location.assign(started.authorization_url);
  } catch (error) {
    $("#orcidLinkError").textContent = error.message;
    $("#orcidLinkError").hidden = false;
  }
}

function handleOrcidOAuthResult() {
  const url = new URL(window.location.href);
  const result = url.searchParams.get("orcid_oauth");
  if (!result) return;
  const messages = {
    connected: ["ORCID iD authenticated and linked", false],
    cancelled: ["ORCID sign-in was cancelled", false],
    "workspace-changed": ["The open CV changed during ORCID sign-in. Try again from this CV.", true],
    "link-error": ["ORCID sign-in completed, but the iD could not be linked to this CV. Open the ORCID dialog to retry.", true],
    error: ["ORCID sign-in could not be completed. Please try again.", true],
  };
  const [message, isError] = messages[result] || messages.error;
  setStatus(message, { error: isError });
  url.searchParams.delete("orcid_oauth");
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function renderOrcidCandidates(data) {
  const container = $("#orcidDiscoveryResults");
  if (data.auto_linked) {
    container.innerHTML = `<div class="orcidCandidate"><strong>Linked ${escapeHtml(data.orcid_id)}</strong><span>Exact-name high-confidence public ORCID match.</span></div>`;
    $("#orcidLinkValue").value = data.orcid_id;
    return;
  }
  if (data.warning) {
    container.innerHTML = `<p>${escapeHtml(data.warning)}</p>`;
    return;
  }
  container.innerHTML = (data.candidates || []).map((candidate) => `
    <button class="orcidCandidate" type="button" data-orcid="${escapeHtml(candidate.orcid_id)}">
      <strong>${escapeHtml(candidate.name || candidate.orcid_id)}</strong>
      <span>${escapeHtml(candidate.orcid_id)}${candidate.institutions?.length ? ` · ${escapeHtml(candidate.institutions.join(", "))}` : ""}</span>
    </button>
  `).join("") || "<p>No confident public ORCID match found. Paste the iD below.</p>";
  $$(".orcidCandidate[data-orcid]").forEach((button) => button.addEventListener("click", () => {
    $("#orcidLinkValue").value = button.dataset.orcid;
  }));
}

async function discoverOrcid() {
  const container = $("#orcidDiscoveryResults");
  container.innerHTML = "<p>Searching the public ORCID directory…</p>";
  try {
    const data = await api("/api/orcid/discover", { method: "POST" });
    renderOrcidCandidates(data);
    if (data.auto_linked) {
      await loadConnections();
      await loadPersonIdentifiers();
      await loadOnboarding();
      pollAutomaticInstitutionMapping();
    }
  } catch (error) {
    container.innerHTML = `<p class="fieldError">${escapeHtml(error.message)}</p>`;
  }
}

async function linkOrcid(event) {
  event.preventDefault();
  const error = $("#orcidLinkError");
  try {
    const data = await api("/api/orcid/link", {
      method: "POST",
      body: JSON.stringify({ orcid_id: $("#orcidLinkValue").value }),
    });
    $("#connectionOrcid").value = data.orcid_id;
    $("#orcidLinkDialog").close();
    await loadConnections();
    await loadPersonIdentifiers();
    await loadOnboarding();
    pollAutomaticInstitutionMapping();
  } catch (caught) {
    error.textContent = caught.message;
    error.hidden = false;
  }
}

async function useExampleDatabase() {
  await api("/api/database/use-example", { method: "POST" });
  setStatus("Example database loaded");
  window.location.reload();
}

function setNewDatabaseError(message = "") {
  const field = $("#newDatabaseNameField");
  const input = $("#newDatabaseName");
  const error = $("#newDatabaseError");
  if (!field || !input || !error) return;
  field.classList.toggle("invalid", Boolean(message));
  input.setAttribute("aria-invalid", message ? "true" : "false");
  error.textContent = message;
  error.hidden = !message;
}

function openNewDatabaseDialog() {
  const dialog = $("#newDatabaseDialog");
  const input = $("#newDatabaseName");
  if (!dialog || !input) return;
  input.value = "workspace";
  setNewDatabaseError("");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  input.focus();
  input.select();
}

function closeNewDatabaseDialog() {
  const dialog = $("#newDatabaseDialog");
  if (!dialog) return;
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}

async function createBlankDatabase(event) {
  event.preventDefault();
  const name = $("#newDatabaseName")?.value.trim() || "";
  if (!name) {
    setNewDatabaseError("Enter a database name.");
    return;
  }
  setNewDatabaseError("");
  try {
    await api("/api/database/create", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    setStatus("Blank database created");
    closeNewDatabaseDialog();
    window.location.reload();
  } catch (error) {
    setNewDatabaseError(error.message || "Could not create database.");
    setStatus(error.message);
  }
}

async function useDatabasePath(path) {
  await api("/api/database/use", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
  setStatus(`Loaded ${path}`);
  window.location.reload();
}

async function importDatabaseFile(file) {
  if (!file) return;
  if (file.path) {
    await useDatabasePath(file.path);
    return;
  }
  const form = new FormData();
  form.append("file", file);
  await api("/api/database/import", {
    method: "POST",
    body: form,
  });
  setStatus(`Loaded ${file.name}`);
  window.location.reload();
}

async function chooseDatabaseFile() {
  try {
    const data = await api("/api/database/choose", { method: "POST" });
    if (data.cancelled) return;
    setStatus(`Loaded ${data.active_name}`);
    window.location.reload();
  } catch (error) {
    setStatus(error.message);
  }
}

async function loadCvImportSettings() {
  if (!$("#cvImportProvider")) return;
  const data = await api("/api/cv-import/settings");
  state.cvImport = data;
  const configurationAllowed = data.configuration_allowed !== false;
  const settingsControls = $("#cvImportSettingsControls");
  const managedNotice = $("#managedLlmNotice");
  if (settingsControls) settingsControls.hidden = !configurationAllowed;
  if (managedNotice) managedNotice.hidden = configurationAllowed;
  $("#cvImportProvider").value = data.provider || "bundled_llama";
  $("#cvImportOllamaUrl").value = data.ollama_url || "http://127.0.0.1:11434";
  $("#cvImportOllamaModel").value = data.ollama_model || "llama3.1:8b";
  $("#cvImportApiBaseUrl").value = data.api_base_url || "https://api.openai.com/v1";
  const apiModel = data.api_model || "gpt-4.1-mini";
  $("#cvImportApiModel").value = apiModel;
  $("#cvImportOpenAiModel").value = OPENAI_MODEL_PRESETS.has(apiModel) ? apiModel : "custom";
  $("#cvImportApiKey").value = "";
  $("#cvImportApiKey").placeholder = data.api_key_set ? "Saved locally; paste to replace" : "Paste API key";
  $("#cvImportKeyStatus").textContent = data.api_key_set ? "API key saved locally" : "No API key";
  if (!configurationAllowed) {
    $("#cvImportOllamaFields").hidden = true;
    $("#cvImportApiFields").hidden = true;
    return;
  }
  updateCvImportProviderVisibility();
}

async function refreshLlmImportPolicy() {
  await Promise.all([loadCvImportSettings(), loadOnboarding()]);
  return usesManagedLlm();
}

function updateCvImportProviderVisibility() {
  const providerSelect = $("#cvImportProvider");
  if (!providerSelect) return;
  const provider = providerSelect.value;
  const ollamaFields = $("#cvImportOllamaFields");
  const apiFields = $("#cvImportApiFields");
  const apiBaseUrlField = $("#cvImportApiBaseUrlField");
  const openAiModelField = $("#cvImportOpenAiModelField");
  const apiModelField = $("#cvImportApiModelField");
  const openAiModel = $("#cvImportOpenAiModel");
  if (provider === "openai") {
    $("#cvImportApiBaseUrl").value = "https://api.openai.com/v1";
  }
  if (ollamaFields) ollamaFields.hidden = provider !== "ollama";
  if (apiFields) apiFields.hidden = !["openai", "openai_compatible"].includes(provider);
  if (apiBaseUrlField) apiBaseUrlField.hidden = provider !== "openai_compatible";
  if (openAiModelField) openAiModelField.hidden = provider !== "openai";
  if (apiModelField) apiModelField.hidden = provider === "openai" ? openAiModel?.value !== "custom" : provider !== "openai_compatible";
  if (provider === "openai" && openAiModel && openAiModel.value !== "custom") {
    $("#cvImportApiModel").value = openAiModel.value;
  }
}

async function saveCvImportSettings() {
  if (!$("#cvImportProvider")) return;
  const testsConnection = ["openai", "openai_compatible"].includes($("#cvImportProvider").value);
  const apiKeyInput = $("#cvImportApiKey").value;
  if ($("#cvImportProvider").value === "openai" && apiKeyInput.trim() && !looksLikeOpenAiApiKey(apiKeyInput)) {
    setStatus("OpenAI API keys should start with sk-. Clear the key field or paste a valid OpenAI API key.");
    return;
  }
  if (testsConnection) {
    setStatus("Testing API connection...");
  }
  setActionButtons(true);
  try {
    await api("/api/cv-import/settings", {
      method: "PUT",
      body: JSON.stringify({
        provider: $("#cvImportProvider").value,
        ollama_url: $("#cvImportOllamaUrl").value,
        ollama_model: $("#cvImportOllamaModel").value,
        api_base_url: $("#cvImportApiBaseUrl").value,
        api_model: $("#cvImportProvider").value === "openai" && $("#cvImportOpenAiModel").value !== "custom"
          ? $("#cvImportOpenAiModel").value
          : $("#cvImportApiModel").value,
        api_key: normalizedApiKeyInput(apiKeyInput),
      }),
    });
    setStatus(testsConnection ? "API connection verified; CV import settings saved" : "CV import settings saved");
    await loadCvImportSettings();
  } catch (error) {
    setStatus(error.message);
  } finally {
    setActionButtons(false);
  }
}

async function importCvFiles(files) {
  files = Array.from(files || []);
  if (!files.length) return;
  if (!requirePlusUi()) return;
  let managedLlm = false;
  try {
    managedLlm = await refreshLlmImportPolicy();
  } catch (error) {
    setStatus(`Could not confirm the CV-import service: ${error.message}`, { error: true });
    return;
  }
  if (managedLlm) {
    closeManagedLlmOnboarding();
  } else if (state.onboarding?.enabled && !state.onboarding.llm_configured) {
    state.pendingCvImportFiles = files;
    const coach = $("#onboardingCoach");
    if (coach) coach.hidden = true;
    clearOnboardingTarget();
    showLlmOnboardingDialog();
    return;
  }
  if (state.onboarding?.step === "import_cv") {
    const coach = $("#onboardingCoach");
    if (coach) coach.hidden = true;
    clearOnboardingTarget();
  }
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  const startedAt = Date.now();
  const fileList = files.map((file) => `- ${file.name}`).join("\n");
  const progressText = () => {
    const elapsed = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
    const phase = elapsed < 10
      ? "Uploading and extracting text"
      : elapsed < 45
        ? "Running import parser"
        : "Still working; LLM imports can take a few minutes";
    return [
      `Importing ${files.length} CV document${files.length === 1 ? "" : "s"}...`,
      "",
      fileList,
      "",
      `${phase} (${elapsed}s elapsed)`,
    ].join("\n");
  };
  const stopProcessing = startProcessing(
    `Importing ${files.length} CV document${files.length === 1 ? "" : "s"}…`,
    progressText(),
  );
  setActionButtons(true);
  try {
    const uploadPath = state.cloud.enabled && state.cloud.workspace?.background_jobs
      ? "/api/cloud/jobs/cv-import"
      : "/api/cv-import/upload";
    const submit = uploadPath.startsWith("/api/cloud/jobs/") ? submitCloudJob : api;
    let data = await submit(uploadPath, {
      method: "POST",
      body: form,
    });
    if (data.background && data.job?.id) {
      state.cloud.activeJob = data.job;
      setCloudJobControls(true);
      data = await waitForCloudJob(data.job.id);
    }
    const publicationPart = data.publications_inserted ? `, ${data.publications_inserted} publications` : "";
    const narrativePart = data.narratives_imported ? `, ${data.narratives_imported} narrative report${data.narratives_imported === 1 ? "" : "s"}` : "";
    const rememberedPart = data.staged?.remembered_rejections ? ` ${data.staged.remembered_rejections} previously rejected candidate${data.staged.remembered_rejections === 1 ? "" : "s"} skipped.` : "";
    const stagedPart = data.candidates_staged ? `${data.candidates_staged} candidates staged for review.${rememberedPart}` : rememberedPart.trim();
    const reviewText = (data.warnings || []).length ? " Review the import log for details." : "";
    appendConsole(JSON.stringify(data, null, 2));
    setStatus(stagedPart || `Imported ${data.entries_inserted || 0} entries, ${data.contributions_inserted || 0} Contributions to Science${publicationPart}${narrativePart}.${reviewText}`);
    await loadSummary();
    await loadImportInbox();
    await loadMetrics();
    await loadEntries();
    await loadPublications();
    await loadPerson();
    await loadNarrativeReport();
    await loadBiosketch();
    if (data.candidates_staged) await openImportReview(data);
    await loadOnboarding();
  } catch (error) {
    setStatus(error.message, { error: true });
    if (state.onboarding?.step === "import_cv") renderOnboardingCoach();
  } finally {
    stopProcessing();
    setActionButtons(false);
    if ($("#cvImportFileInput")) $("#cvImportFileInput").value = "";
  }
}

async function loadConnections() {
  const data = await api("/api/connections");
  state.connections = data;
  $("#connectionOrcid").value = data.orcid_id || "";
  $("#connectionZoteroKey").value = "";
  renderZoteroLibraries();
  $("#connectionZoteroLibrary").value = data.zotero_library_value || "";
  $("#connectionZoteroSource").value = data.zotero_source_mode || "my_publications";
  renderZoteroCollections();
  $("#connectionStatus").textContent = data.zotero_api_key_set
    ? "Zotero key saved"
    : (data.orcid_id ? "ORCID linked; Zotero optional" : "No publication source linked");
  updateZoteroSourceVisibility();
  await loadOrcidOAuthStatus();
  await loadZoteroOAuthStatus();
}

async function saveConnections(event) {
  event.preventDefault();
  const sourceMode = $("#connectionZoteroSource").value;
  const selected = sourceMode === "collection" ? selectedZoteroCollection() : null;
  const selectedLibrary = selectedZoteroLibrary();
  const orcidId = $("#connectionOrcid").value.trim();
  await api("/api/connections", {
    method: "PUT",
    body: JSON.stringify({
      orcid_id: $("#connectionOrcid").value,
      zotero_api_key: $("#connectionZoteroKey").value,
      zotero_library_value: $("#connectionZoteroLibrary").value,
      zotero_group_name: selectedLibrary?.type === "groups" ? selectedLibrary.name : "",
      zotero_source_mode: sourceMode,
      zotero_collection_key: selected?.key || "",
      zotero_collection_name: selected?.name || "",
    }),
  });
  $("#connectionZoteroKey").value = "";
  setStatus("Connections saved");
  await loadConnections();
  if (state.connections.zotero_api_key_set) {
    await testZoteroConnection();
  }
  await loadPersonIdentifiers();
  await loadOnboarding();
  if (orcidId) pollAutomaticInstitutionMapping();
}

function renderZoteroLibraries() {
  const current = state.connections || {};
  const existing = [];
  if (current.zotero_library_value) {
    existing.push({
      type: current.zotero_library_type || "users",
      id: current.zotero_library_id || "",
      name: current.zotero_group_name || current.zotero_library_value,
      kind: current.zotero_library_type === "groups" ? "Group library" : "Personal library",
    });
  }
  const merged = [...existing, ...state.zoteroLibraries];
  const seen = new Set();
  const libraries = merged.filter((item) => {
    const id = `${item.type}:${item.id}`;
    if (!item.id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  $("#connectionZoteroLibrary").innerHTML = [
    '<option value="">Auto-detect from key</option>',
    ...libraries.map((item) => {
      const label = item.kind ? `${item.name} (${item.kind})` : item.name;
      return `<option value="${escapeHtml(`${item.type}:${item.id}`)}">${escapeHtml(label)}</option>`;
    }),
  ].join("");
}

function selectedZoteroLibrary() {
  const value = $("#connectionZoteroLibrary").value || "";
  const separator = value.indexOf(":");
  if (separator < 0) return null;
  const type = value.slice(0, separator);
  const id = value.slice(separator + 1);
  return state.zoteroLibraries.find((item) => item.type === type && String(item.id) === id) || null;
}

function selectedZoteroCollection() {
  const value = $("#connectionZoteroCollection").value || "";
  return state.zoteroCollections.find((item) => `${item.mode}:${item.key || ""}` === value) || null;
}

function renderZoteroCollections() {
  const current = state.connections || {};
  const existing = [];
  if (current.zotero_collection_key || current.zotero_collection_name) {
    existing.push({
      mode: "collection",
      key: current.zotero_collection_key || "",
      name: current.zotero_collection_name || current.zotero_collection_key || "Selected collection",
    });
  }
  const base = [
    { mode: "my_publications", key: "", name: "My Publications" },
    { mode: "library", key: "", name: "Whole library" },
  ];
  const merged = [...base, ...existing, ...state.zoteroCollections];
  const seen = new Set();
  state.zoteroCollections = merged.filter((item) => {
    const id = `${item.mode}:${item.key || ""}`;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  $("#connectionZoteroCollection").innerHTML = state.zoteroCollections
    .filter((item) => item.mode === "collection")
    .map((item) => `<option value="${escapeHtml(`${item.mode}:${item.key || ""}`)}">${escapeHtml(item.path || item.name)}</option>`)
    .join("");
  if (current.zotero_collection_key) {
    $("#connectionZoteroCollection").value = `collection:${current.zotero_collection_key}`;
  }
}

function updateZoteroSourceVisibility() {
  const source = $("#connectionZoteroSource").value;
  const collectionWrap = $("#connectionZoteroCollectionWrap");
  const collectionSelect = $("#connectionZoteroCollection");
  const loadCollectionsButton = $("#loadZoteroCollections");
  const collectionMode = source === "collection";
  if (collectionWrap) collectionWrap.hidden = !collectionMode;
  if (collectionSelect) {
    collectionSelect.disabled = !collectionMode;
    if (!collectionMode) collectionSelect.value = "";
  }
  if (loadCollectionsButton) loadCollectionsButton.disabled = !collectionMode;
}

async function connectZotero() {
  if (state.cloud.enabled) {
    const started = await api("/gateway/zotero/oauth/start", { method: "POST" });
    window.location.assign(started.authorization_url);
    return;
  }
  const data = await api("/api/zotero/connect-url");
  window.open(data.url, "_blank", "noopener,width=980,height=760,left=0,top=0");
  setStatus(data.oauth_available ? "Opening Zotero authorization" : "Opening Zotero key setup");
  if (state.onboarding?.step === "zotero") {
    state.zoteroSetupOpened = true;
    renderOnboardingCoach();
    window.setTimeout(() => $("#connectionZoteroKey")?.focus(), 400);
  }
}

async function loadZoteroOAuthStatus() {
  const keyWrap = $("#connectionZoteroKeyWrap");
  const connect = $("#connectZotero");
  const disconnect = $("#disconnectZotero");
  if (!state.cloud.enabled) {
    if (keyWrap) keyWrap.hidden = false;
    if (disconnect) disconnect.hidden = true;
    return;
  }
  if (keyWrap) keyWrap.hidden = true;
  state.zoteroOauth = await api("/gateway/zotero/oauth/status");
  connect.disabled = !state.zoteroOauth.configured;
  connect.textContent = state.zoteroOauth.connected ? "Reconnect Zotero" : "Connect Zotero";
  disconnect.hidden = !state.zoteroOauth.connected;
  if (!state.zoteroOauth.configured) {
    $("#connectionStatus").textContent = "Zotero sign-in is not configured";
  } else if (state.zoteroOauth.connected) {
    const identity = state.zoteroOauth.username || state.zoteroOauth.zotero_user_id;
    $("#connectionStatus").textContent = state.zoteroOauth.can_write
      ? `Zotero connected with write access${identity ? ` as ${identity}` : ""}`
      : `Zotero connected read-only${identity ? ` as ${identity}` : ""}. Reconnect to enable profile sync updates.`;
    if (!state.zoteroLibraries.length) {
      try {
        await testZoteroConnection();
      } catch (error) {
        console.warn("Zotero libraries could not be loaded automatically:", error);
      }
    }
  }
}

async function disconnectZotero() {
  await api("/gateway/zotero/oauth/connection", { method: "DELETE" });
  state.zoteroLibraries = [];
  state.zoteroCollections = [];
  await loadConnections();
  setStatus("Zotero disconnected");
}

function handleZoteroOAuthResult() {
  const url = new URL(window.location.href);
  const result = url.searchParams.get("zotero_oauth");
  if (!result) return;
  const messages = {
    connected: ["Zotero connected securely", false],
    cancelled: ["Zotero sign-in was cancelled", false],
    "workspace-changed": ["The open CV changed during Zotero sign-in. Please try again.", true],
    "link-error": ["Zotero sign-in could not be completed. Please try again.", true],
  };
  const [message, isError] = messages[result] || messages["link-error"];
  setStatus(message, { error: isError });
  url.searchParams.delete("zotero_oauth");
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

async function loadZoteroCollections() {
  const data = await api("/api/zotero/collections");
  state.zoteroCollections = data.collections || [];
  renderZoteroCollections();
  updateZoteroSourceVisibility();
  state.connections = {
    ...state.connections,
    zotero_library_type: data.library_type,
    zotero_library_id: data.library_id,
    zotero_library_value: `${data.library_type}:${data.library_id}`,
  };
  renderZoteroLibraries();
  $("#connectionZoteroLibrary").value = state.connections.zotero_library_value;
  $("#connectionStatus").textContent = `${state.zoteroCollections.filter((item) => item.mode === "collection").length} collections loaded`;
  setStatus("Zotero collections loaded");
}

async function testZoteroConnection() {
  const data = await api("/api/zotero/status");
  state.zoteroLibraries = data.libraries || [];
  if (data.library) {
    state.connections = {
      ...state.connections,
      zotero_library_type: data.library.type,
      zotero_library_id: data.library.id,
      zotero_library_value: `${data.library.type}:${data.library.id}`,
      zotero_group_name: data.library.type === "groups" ? data.library.name : "",
    };
  }
  renderZoteroLibraries();
  if (state.connections.zotero_library_value) {
    $("#connectionZoteroLibrary").value = state.connections.zotero_library_value;
  }
  $("#connectionStatus").textContent = data.ok ? `${data.message} ${data.collection_count ?? 0} collections found.` : data.message;
  setStatus(data.ok ? "Zotero link established" : data.message);
}

async function loadMetrics() {
  const data = await api("/api/metrics");
  const pubs = data.publications || {};
  $("#metricsGrid").innerHTML = [
    metricCard(
      "Total Publications",
      pubs.visible || 0,
      "All publications included in your CV metrics, including preprints and other formats. Records marked hidden or problematic are excluded.",
      "total-publications",
    ),
    metricCard(
      "Peer Reviewed Publications",
      pubs.peer_reviewed || 0,
      "Publications categorized as peer reviewed. This is a subset of Total Publications.",
      "peer-reviewed",
    ),
    metricCard(
      "Total Citations",
      pubs.openalex_cited_by_total || 0,
      "Cumulative citations to your work as reported by OpenAlex.",
      "total-citations",
    ),
    metricCard(
      "ORCID Matched Publications",
      pubs.orcid_matched || 0,
      "Publications in this CV that are linked to a work on your ORCID record.",
      "orcid-matched",
    ),
    metricCard(
      "Publications with Citation Data",
      pubs.citation_metric_count || 0,
      "Publications for which VitaMine has an OpenAlex citation count. This measures data coverage, not the impact of those publications.",
      "citation-coverage",
    ),
    metricCard(
      "Publications with Impact Factors",
      pubs.impact_factor_count || 0,
      "Publications whose journal has a stored Journal Impact Factor. The Impact Factor is a journal-level citation average and does not measure the quality of an individual paper.",
      "impact-factor-coverage",
    ),
    metricCard(
      "Hidden / Problem Records",
      pubs.suppressed || 0,
      "Records excluded from normal CV output and public metrics because they were marked as duplicates, uncertain matches, or other problems.",
      "hidden-records",
    ),
  ].join("");
  $("#yearMetrics").innerHTML = (data.by_year || [])
    .map((row) => `<span>${row.year}: <strong>${row.count}</strong></span>`)
    .join("");
  $("#venueMetrics").innerHTML = (data.top_venues || [])
    .map((row) => {
      const impact = row.impact_factor == null ? "" : ` · IF ${row.impact_factor}`;
      return `<span>${row.venue}: <strong>${row.count}</strong>${impact}</span>`;
    })
    .join("");
  renderCitationProfile(data.citation_profile || {});
}

function metricCard(label, value, description, key) {
  const tooltipId = `metric-tooltip-${key}`;
  return `
    <article class="metricCard" tabindex="0" aria-describedby="${tooltipId}">
      <strong>${escapeHtml(formatMetricNumber(value))}</strong>
      <span class="metricLabel">${escapeHtml(label)}<i aria-hidden="true">i</i></span>
      <span id="${tooltipId}" class="metricTooltip">${escapeHtml(description)}</span>
    </article>`;
}

function formatMetricNumber(value) {
  return Number(value || 0).toLocaleString();
}

function formatMetricDecimal(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toLocaleString(undefined, {
    minimumFractionDigits: Number.isInteger(number) ? 0 : 1,
    maximumFractionDigits: 2,
  });
}

function citationAxisMax(value) {
  const amount = Number(value || 0);
  if (!amount) return 0;
  const magnitude = 10 ** Math.floor(Math.log10(amount));
  const normalized = amount / magnitude;
  const step = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10].find((candidate) => normalized <= candidate) || 10;
  return step * magnitude;
}

function renderCitationProfile(profile) {
  const coverage = $("#citationProfileCoverage");
  const table = $("#citationProfileTable");
  const chart = $("#citationProfileChart");
  if (!coverage || !table || !chart) return;
  const all = profile.all || {};
  const recent = profile.since_yearly_citations || profile.since_publication_year || {};
  const firstLast = profile.first_last_author || {};
  const firstLastRecent = profile.first_last_author_since_yearly_citations || {};
  const sinceYear = profile.since_year || "";
  coverage.textContent = `${formatMetricNumber(profile.citation_metric_count || 0)} publications with OpenAlex citation data`;
  table.innerHTML = `
    <span class="citationMetricGroup citationMetricGroupFirst">All Publications</span>
    <div></div>
    <strong>All</strong>
    <strong>Since ${escapeHtml(sinceYear)}</strong>
    <span>Citations</span>
    <strong>${formatMetricNumber(all.citations)}</strong>
    <strong>${formatMetricNumber(recent.citations)}</strong>
    <span>h-index</span>
    <button type="button" class="citationMetricButton" data-h-index-scope="all" title="View h-index over time">${formatMetricNumber(all.h_index)}</button>
    <button type="button" class="citationMetricButton" data-h-index-scope="all" title="View h-index over time">${formatMetricNumber(recent.h_index)}</button>
    <span>i10-index</span>
    <strong>${formatMetricNumber(all.i10_index)}</strong>
    <strong>${formatMetricNumber(recent.i10_index)}</strong>
    <span class="citationMetricGroup">First/Last-author publications</span>
    <div></div>
    <strong>All</strong>
    <strong>Since ${escapeHtml(sinceYear)}</strong>
    <span>Citations</span>
    <strong>${formatMetricNumber(firstLast.citations)}</strong>
    <strong>${formatMetricNumber(firstLastRecent.citations)}</strong>
    <span>h-index</span>
    <button type="button" class="citationMetricButton" data-h-index-scope="first_last" title="View h-index over time">${formatMetricNumber(firstLast.h_index)}</button>
    <button type="button" class="citationMetricButton" data-h-index-scope="first_last" title="View h-index over time">${formatMetricNumber(firstLastRecent.h_index)}</button>
    <span>i10-index</span>
    <strong>${formatMetricNumber(firstLast.i10_index)}</strong>
    <strong>${formatMetricNumber(firstLastRecent.i10_index)}</strong>
  `;
  const years = profile.by_year || [];
  const maxCitations = Math.max(...years.map((row) => Number(row.citations || 0)), 0);
  const axisMax = citationAxisMax(maxCitations);
  chart.innerHTML = years.length && axisMax
    ? `<div class="citationChartPlot">
        <div class="citationChartBars">${years.map((row, index) => {
          const height = Math.max(6, Number(row.citations || 0) / axisMax * 100);
          const year = escapeHtml(row.year);
          return `<button
            type="button"
            class="citationBarItem"
            data-citation-year-index="${index}"
            aria-label="${year}: ${formatMetricNumber(row.citations)} citations. Select yearly details."
            aria-pressed="false"
            aria-describedby="citationYearDetail"
          >
            <span class="citationBarValue">${formatMetricNumber(row.citations)}</span>
            <span class="citationBarTrack"><span class="citationBar" style="height:${height.toFixed(1)}%"></span></span>
            <strong>${year}</strong>
          </button>`;
        }).join("")}</div>
        <div class="citationAxis" aria-hidden="true">
          <div></div>
          <div class="citationAxisTicks">
            ${[1, 0.75, 0.5, 0.25, 0].map((fraction) => `
              <span style="top:${((1 - fraction) * 100).toFixed(1)}%">${formatMetricNumber(axisMax * fraction)}</span>
            `).join("")}
          </div>
          <div></div>
        </div>
      </div>
      <div id="citationYearDetail" class="citationYearDetail isEmpty" aria-live="polite"></div>`
    : `<p class="emptyState">No yearly OpenAlex citation counts available yet.</p>`;
  if (years.length) {
    requestAnimationFrame(() => {
      const bars = chart.querySelector(".citationChartBars");
      if (bars) bars.scrollLeft = bars.scrollWidth;
    });
    const detail = chart.querySelector("#citationYearDetail");
    const barItems = chart.querySelectorAll(".citationBarItem");
    const showDetail = (item) => {
      if (!detail) return;
      const row = years[Number(item.dataset.citationYearIndex)];
      if (!row) return;
      const impactFactorCount = Number(row.impact_factor_count || 0);
      detail.innerHTML = `
        <strong>${escapeHtml(row.year)} details</strong>
        <dl>
          <div>
            <dt>Citations received</dt>
            <dd>${formatMetricNumber(row.citations)}</dd>
          </div>
          <div>
            <dt>First/last-author citations</dt>
            <dd>${formatMetricNumber(row.first_last_author_citations)}</dd>
          </div>
          <div>
            <dt>Publications published</dt>
            <dd>${formatMetricNumber(row.publications_published)}</dd>
          </div>
          <div>
            <dt>Combined journal Impact Factors</dt>
            <dd>${impactFactorCount ? formatMetricDecimal(row.impact_factor_sum) : "—"}</dd>
          </div>
        </dl>
        <small>
          Citations are citations received during this year. The Impact Factor figure sums the journal-level
          values available for ${formatMetricNumber(impactFactorCount)}
          ${impactFactorCount === 1 ? "publication" : "publications"} published that year; it is not a paper-quality score.
        </small>
      `;
      detail.classList.remove("isEmpty");
      barItems.forEach((candidate) => {
        const selected = candidate === item;
        candidate.toggleAttribute("aria-current", selected);
        candidate.setAttribute("aria-pressed", String(selected));
      });
    };
    const currentYear = String(new Date().getFullYear());
    const defaultIndex = years.findIndex((row) => String(row.year) === currentYear);
    showDetail(barItems[defaultIndex >= 0 ? defaultIndex : barItems.length - 1]);
    barItems.forEach((item) => {
      item.addEventListener("click", () => showDetail(item));
    });
  }
  table.querySelectorAll("[data-h-index-scope]").forEach((button) => {
    button.addEventListener("click", () => openHIndexHistory(button.dataset.hIndexScope));
  });
}

async function openCitationExplorer() {
  const dialog = $("#citationExplorerDialog");
  if (!dialog) return;
  if (!state.citationExplorer.data) {
    $("#citationExplorerList").innerHTML = `<p class="emptyState">Loading citation profile…</p>`;
    dialog.showModal();
    try {
      state.citationExplorer.data = await api("/api/citation-profile/publications");
    } catch (error) {
      $("#citationExplorerList").innerHTML = `<p class="emptyState">${escapeHtml(error.message || "Citation profile could not load.")}</p>`;
      return;
    }
  } else if (!dialog.open) {
    dialog.showModal();
  }
  renderCitationExplorer();
}

function citationExplorerPublications() {
  const rows = state.citationExplorer.data?.publications || [];
  const filtered = state.citationExplorer.firstLastOnly
    ? rows.filter((row) => ["first", "last", "first_last"].includes(row.authorship))
    : [...rows];
  return filtered.sort((left, right) => {
    if (state.citationExplorer.sort === "year") {
      const yearDifference = Number(right.year || 0) - Number(left.year || 0);
      if (yearDifference) return yearDifference;
    }
    const citationDifference = Number(right.citations || 0) - Number(left.citations || 0);
    if (citationDifference) return citationDifference;
    return String(left.title || left.raw_citation || "").localeCompare(String(right.title || right.raw_citation || ""));
  });
}

function citationDoiHref(value) {
  const doi = String(value || "")
    .trim()
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
    .replace(/^doi:\s*/i, "");
  if (!/^10\.\d{4,9}\/\S+$/i.test(doi)) return "";
  return `https://doi.org/${encodeURIComponent(doi)}`;
}

function citationTitleMarkup(title, doi) {
  const text = escapeHtml(title || "Untitled publication");
  const href = citationDoiHref(doi);
  return href
    ? `<a class="citationTitleLink" href="${href}" target="_blank" rel="noopener noreferrer">${text}</a>`
    : text;
}

function citationAuthorsMarkup(authors, researcherAuthorIndexes = []) {
  const parts = String(authors || "").split(",").map((author) => author.trim()).filter(Boolean);
  const highlighted = new Set((researcherAuthorIndexes || []).map(Number));
  if (!parts.length) return "Authors unavailable";
  return parts.map((author, index) => highlighted.has(index)
    ? `<strong class="citationResearcherAuthor">${escapeHtml(author)}</strong>`
    : escapeHtml(author)
  ).join(", ");
}

function renderCitationExplorer() {
  const container = $("#citationExplorerList");
  if (!container) return;
  const rows = citationExplorerPublications();
  const hIndex = rows.reduce((score, row, index) => Number(row.citations || 0) >= index + 1 ? index + 1 : score, 0);
  const summary = $("#citationExplorerSummary");
  const hint = $("#citationExplorerHint");
  if (summary) summary.textContent = `${formatMetricNumber(rows.length)} publications with OpenAlex citation data`;
  if (hint) {
    hint.textContent = state.citationExplorer.sort === "citations"
      ? `The teal divider marks the ${formatMetricNumber(hIndex)} papers that currently meet this h-index.`
      : "Publication year order does not show an h-index divider. Switch to citations to see it.";
  }
  document.querySelectorAll("[data-citation-sort]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.citationSort === state.citationExplorer.sort));
  });
  const toggle = $("#citationFirstLastOnly");
  if (toggle) toggle.checked = state.citationExplorer.firstLastOnly;
  if (!rows.length) {
    container.innerHTML = `<p class="emptyState">No publications with OpenAlex citation data are available yet.</p>`;
    return;
  }
  const ordered = rows.map((row, index) => {
    const title = row.title || row.raw_citation || "Untitled publication";
    const authors = citationAuthorsMarkup(row.authors, row.researcher_author_indexes);
    const venueParts = [row.venue, row.year].filter(Boolean).map(escapeHtml);
    const marker = state.citationExplorer.sort === "citations" && hIndex && index + 1 === hIndex
      ? `<div class="citationHIndexMarker"><span>h-index ${formatMetricNumber(hIndex)}</span></div>`
      : "";
    return `<article class="citationPublicationRow">
      <div class="citationPublicationMain">
        <h3>${citationTitleMarkup(title, row.doi)}</h3>
        <p>${authors}${venueParts.length ? ` · ${venueParts.join(" · ")}` : ""}</p>
      </div>
      <button type="button" class="citationCountButton" data-cited-publication-id="${Number(row.id)}" aria-label="Show works citing ${escapeHtml(title)}">
        <strong>${formatMetricNumber(row.citations)}</strong><span>Citations</span>
      </button>
    </article>${marker}`;
  });
  container.innerHTML = ordered.join("");
  container.querySelectorAll("[data-cited-publication-id]").forEach((button) => {
    button.addEventListener("click", () => openCitingWorks(Number(button.dataset.citedPublicationId), 1, button));
  });
}

function renderPaperCitationHistory(publication) {
  const chart = $("#paperCitationHistoryChart");
  if (!chart) return;
  const years = publication?.citation_years || [];
  const max = Math.max(...years.map((row) => Number(row.citations || 0)), 0);
  if (!years.length || !max) {
    chart.innerHTML = `<p class="emptyState">No annual OpenAlex citation counts are available for this paper yet.</p>`;
    return;
  }
  chart.innerHTML = `<div class="paperCitationBars">${years.map((row) => {
    const height = Math.max(5, Number(row.citations || 0) / max * 100);
    return `<div class="paperCitationBarItem" title="${escapeHtml(row.year)}: ${formatMetricNumber(row.citations)} citations">
      <span>${formatMetricNumber(row.citations)}</span><i style="height:${height.toFixed(1)}%"></i><strong>${escapeHtml(row.year)}</strong>
    </div>`;
  }).join("")}</div>`;
}

function setCitationCitedByLoading(loading, trigger = null) {
  const loader = $("#citationCitedByLoading");
  if (loader) loader.hidden = !loading;
  const explorer = $("#citationExplorerDialog");
  if (explorer) explorer.setAttribute("aria-busy", String(loading));
  if (trigger) {
    trigger.disabled = loading;
    trigger.setAttribute("aria-busy", String(loading));
  }
}

async function openCitingWorks(publicationId, page = 1, trigger = null) {
  const publication = (state.citationExplorer.data?.publications || []).find((row) => Number(row.id) === Number(publicationId));
  if (!publication) return;
  const dialog = $("#citationCitedByDialog");
  if (!dialog) return;
  const initialLoad = page === 1;
  const loadMore = $("#loadMoreCitingWorks");
  const loadMoreLabel = loadMore?.textContent;
  if (initialLoad) {
    state.citationExplorer.selectedPublication = publication;
    state.citationExplorer.citingWorks = [];
    setCitationCitedByLoading(true, trigger);
  } else if (loadMore) {
    loadMore.disabled = true;
    loadMore.textContent = "Loading…";
  }
  try {
    const data = await api(`/api/citation-profile/publications/${encodeURIComponent(publicationId)}/cited-by?page=${encodeURIComponent(page)}`);
    state.citationExplorer.citingWorks = initialLoad ? (data.works || []) : [...state.citationExplorer.citingWorks, ...(data.works || [])];
    state.citationExplorer.nextCitingPage = data.next_page;
    if (initialLoad) {
      $("#citationCitedByTitle").textContent = publication.title || publication.raw_citation || "Publication";
      renderPaperCitationHistory(publication);
    }
    renderCitingWorks(data.total, data.source);
    if (initialLoad && !dialog.open) dialog.showModal();
  } catch (error) {
    if (initialLoad) {
      const hint = $("#citationExplorerHint");
      if (hint) hint.textContent = error.message || "Citing works could not load. Please try again.";
      setStatus(error.message || "Citing works could not load.", { error: true });
    } else {
      $("#citationCitedBySummary").textContent = error.message || "More citing works could not load.";
    }
  } finally {
    if (initialLoad) setCitationCitedByLoading(false, trigger);
    if (!initialLoad && loadMore) {
      loadMore.disabled = false;
      loadMore.textContent = loadMoreLabel || "Load more";
    }
  }
}

function renderCitingWorks(total, source) {
  const works = state.citationExplorer.citingWorks;
  const list = $("#citationCitedByList");
  const summary = $("#citationCitedBySummary");
  const more = $("#loadMoreCitingWorks");
  if (summary) summary.textContent = `${formatMetricNumber(works.length)} of ${formatMetricNumber(total || works.length)} citing works shown · live data from ${source || "OpenAlex"}`;
  if (list) list.innerHTML = works.length ? works.map((work) => {
    const metadata = [work.venue, work.year].filter(Boolean).map(escapeHtml).join(" · ");
    return `<article class="citationPublicationRow citingWorkRow">
      <div class="citationPublicationMain"><h3>${citationTitleMarkup(work.title, work.doi)}</h3>
        <p>${escapeHtml(work.authors || "Authors unavailable")}${metadata ? ` · ${metadata}` : ""}</p></div>
      <span class="citingWorkCount"><strong>${formatMetricNumber(work.citations)}</strong><span>Citations</span></span>
    </article>`;
  }).join("") : `<p class="emptyState">OpenAlex has no citing works to show for this paper yet.</p>`;
  if (more) more.hidden = !state.citationExplorer.nextCitingPage;
}

function citationNetworkDoi(node) {
  return citationDoiHref(node.doi);
}

function networkNodeTooltip(node) {
  return [node.title, node.authors, [node.venue, node.year].filter(Boolean).join(" · "), `${formatMetricNumber(node.citations)} citations`]
    .filter(Boolean).join("\n");
}

function runCitationNetworkLayout(graph, { reset = false, fit = false } = {}) {
  if (!graph) return;
  graph.layout({
    name: "cose",
    animate: true,
    animationDuration: reset ? 900 : 560,
    padding: 96,
    nodeRepulsion: 18000,
    idealEdgeLength: 195,
    edgeElasticity: 0.22,
    gravity: 0.14,
    numIter: 1400,
    initialTemp: 220,
    coolingFactor: 0.97,
    minTemp: 1,
    nodeDimensionsIncludeLabels: true,
    nodeOverlap: 34,
    componentSpacing: 100,
    randomize: reset,
    fit,
  }).run();
}

function renderCitationNetwork() {
  const chart = $("#citationNetworkGraph");
  const data = state.citationNetwork.data;
  if (!chart || !data) return;
  state.citationNetwork.graph?.destroy();
  state.citationNetwork.graph = null;
  if (!data.cached) { chart.innerHTML = `<div class="citationNetworkPending"><span class="citationPaperSpinner" aria-hidden="true"></span><strong>Preparing your citation network</strong><span>VitaMine is collecting citing papers in the background. You can close this window and check back shortly.</span></div>`; return; }
  if (!window.cytoscape) { chart.innerHTML = `<p class="emptyState">The interactive graph renderer could not load. Please check your connection and reopen this view.</p>`; return; }
  chart.innerHTML = `<div id="citationNetworkCanvas" class="citationNetworkCanvas"></div><aside id="citationNetworkTooltip" class="citationNetworkTooltip" hidden></aside>`;
  const tooltip = $("#citationNetworkTooltip");
  const graph = window.cytoscape({
    container: $("#citationNetworkCanvas"),
    elements: [
      ...data.nodes.map((node) => ({ data: { ...node } })),
      ...data.links.map((link, index) => ({ data: { id: `edge-${index}`, source: link.source, target: link.target } })),
    ],
    style: [
      { selector: "node", style: { "background-fill": "radial-gradient", "background-gradient-stop-colors": "#a8c6d9 #6689a2", "background-gradient-stop-positions": "0% 100%", "border-width": 2, "border-color": "#4c6476", "shadow-blur": 7, "shadow-color": "#405c70", "shadow-opacity": 0.23, "shadow-offset-y": 2, width: "mapData(citations, 0, 1000, 9, 20)", height: "mapData(citations, 0, 1000, 9, 20)", "overlay-opacity": 0, "transition-property": "background-color, border-width, border-color", "transition-duration": "160ms" } },
      { selector: 'node[kind = "own"]', style: { "background-fill": "radial-gradient", "background-gradient-stop-colors": "#48c6b0 #0b7669", "background-gradient-stop-positions": "0% 100%", "border-width": 4, "border-color": "#075e55", "shadow-blur": 12, "shadow-color": "#0c766b", "shadow-opacity": 0.3, width: "mapData(citations, 0, 1000, 24, 46)", height: "mapData(citations, 0, 1000, 24, 46)" } },
      { selector: "node:active", style: { "border-width": 4, "border-color": "#172554" } },
      { selector: "edge", style: { width: 1.8, "line-gradient-stop-colors": "#b4cecd #6f9e9b", "line-gradient-stop-positions": "0% 100%", opacity: 0.68, "curve-style": "bezier" } },
    ],
    wheelSensitivity: 0.18,
    minZoom: 0.2,
    maxZoom: 3,
    layout: { name: "preset" },
  });
  state.citationNetwork.graph = graph;
  runCitationNetworkLayout(graph, { reset: true, fit: true });
  graph.on("mouseover", "node", (event) => { const node = event.target.data(); tooltip.innerHTML = `<strong>${escapeHtml(node.title)}</strong><span>${escapeHtml(node.authors || "Authors unavailable")}</span><span>${escapeHtml([node.venue, node.year].filter(Boolean).join(" · "))}</span><span>${formatMetricNumber(node.citations)} citations</span>`; tooltip.hidden = false; });
  graph.on("mousemove", "node", (event) => { const position = event.renderedPosition; tooltip.style.left = `${Math.min(chart.clientWidth - 270, position.x + 16)}px`; tooltip.style.top = `${Math.min(chart.clientHeight - 130, position.y + 16)}px`; });
  graph.on("mouseout", "node", () => { tooltip.hidden = true; });
  graph.on("tap", "node", (event) => { const href = citationNetworkDoi(event.target.data()); if (href) window.open(href, "_blank", "noopener,noreferrer"); });
  graph.on("free", "node", () => {
    window.clearTimeout(state.citationNetwork.settleTimer);
    state.citationNetwork.settleTimer = window.setTimeout(() => runCitationNetworkLayout(graph), 90);
  });
  $("#citationNetworkSummary").textContent = `${data.nodes.filter((node) => node.kind === "own").length} own papers · ${data.nodes.filter((node) => node.kind === "citing").length} citing papers · drag a dot to arrange the graph${state.citationNetwork.refreshing ? " · updating in background" : ""}`;
}

async function openCitationNetwork() {
  const dialog = $("#citationNetworkDialog"); if (!dialog) return;
  if (!dialog.open) dialog.showModal();
  $("#citationNetworkGraph").innerHTML = `<p class="emptyState">Loading citation network…</p>`;
  try {
    state.citationNetwork.data = await api("/api/citation-network");
    renderCitationNetwork();
    if (!state.citationNetwork.data.cached || state.citationNetwork.data.stale) queueCitationNetworkRefresh();
  } catch (error) { $("#citationNetworkGraph").innerHTML = `<p class="emptyState">${escapeHtml(error.message)}</p>`; }
}

async function queueCitationNetworkRefresh() {
  if (state.citationNetwork.refreshing) return;
  state.citationNetwork.refreshing = true;
  const button = $("#refreshCitationNetwork");
  if (button) { button.disabled = true; button.textContent = "Refreshing in background…"; }
  const path = state.cloud.enabled ? "/api/cloud/jobs/citation-network" : "/api/actions/refresh-citation-network";
  try {
    const submit = state.cloud.enabled ? submitCloudJob : api;
    const result = await submit(path, { method: "POST" });
    if (result.background && result.job?.id) {
      state.cloud.activeJob = result.job;
      setCloudJobControls(true);
      setStatus("Collecting citing papers in the background…");
      waitForCloudJob(result.job.id).then(async (completed) => {
        const size = Number(completed?.database_size_bytes || 0);
        const footprint = size ? ` The CV database is now ${(size / 1024 / 1024).toFixed(1)} MB.` : "";
        setStatus(`Citation network ready.${footprint}`);
        await openCitationNetwork();
      }).catch((error) => {
        $("#citationNetworkGraph").innerHTML = `<p class="emptyState">${escapeHtml(error.message)}</p>`;
      }).finally(() => { state.citationNetwork.refreshing = false; if (button) { button.disabled = false; button.textContent = "Refresh network from OpenAlex"; } renderCitationNetwork(); });
    } else {
      state.citationNetwork.refreshing = false;
      await openCitationNetwork();
    }
  } catch (error) {
    state.citationNetwork.refreshing = false;
    if (button) { button.disabled = false; button.textContent = "Refresh network from OpenAlex"; }
    $("#citationNetworkGraph").innerHTML = `<p class="emptyState">${escapeHtml(error.message || "Citation-network refresh could not start.")}</p>`;
  }
}

async function openHIndexHistory(scope = "all") {
  if (!state.citationExplorer.data) {
    try {
      state.citationExplorer.data = await api("/api/citation-profile/publications");
    } catch (error) {
      setStatus(error.message || "Citation profile could not load.", { error: true });
      return;
    }
  }
  const history = state.citationExplorer.data?.h_index_history || [];
  const dialog = $("#hIndexHistoryDialog");
  const chart = $("#hIndexHistoryChart");
  if (!dialog || !chart) return;
  const label = scope === "first_last" ? "first/last-author h-index" : "h-index";
  $("#hIndexHistoryTitle").textContent = `${label} over time`;
  const max = Math.max(...history.map((row) => Number(row[scope] || 0)), 0);
  chart.innerHTML = history.length && max
    ? `<div class="hIndexBars">${history.map((row) => `<div class="hIndexBarItem" title="${escapeHtml(row.year)}: ${formatMetricNumber(row[scope])}">
        <span>${formatMetricNumber(row[scope])}</span><i style="height:${Math.max(5, Number(row[scope] || 0) / max * 100).toFixed(1)}%"></i><strong>${escapeHtml(row.year)}</strong>
      </div>`).join("")}</div>`
    : `<p class="emptyState">Annual OpenAlex citation counts are needed before this history can be calculated.</p>`;
  if (!dialog.open) dialog.showModal();
}

const MAP_WIDTH = 1000;
const MAP_HEIGHT = 520;
const MAP_ZOOM = 2;
const MAP_MIN_ZOOM = 2;
const MAP_MAX_ZOOM = 6;
const MAP_TILE_SIZE = 256;

function mapWorldSize(zoom) {
  return MAP_TILE_SIZE * (2 ** zoom);
}

function defaultMapOrigin(zoom = MAP_ZOOM) {
  const world = mapWorldSize(zoom);
  return {
    x: (world - MAP_WIDTH) / 2,
    y: (world - MAP_HEIGHT) / 2,
  };
}

function mapOriginForNode(node, zoom = MAP_ZOOM) {
  if (!Number.isFinite(Number(node?.longitude)) || !Number.isFinite(Number(node?.latitude))) {
    return defaultMapOrigin(zoom);
  }
  const tile = lonLatToTile(node.longitude, node.latitude, zoom);
  return clampMapOrigin({
    x: tile.x * MAP_TILE_SIZE - MAP_WIDTH / 2,
    y: tile.y * MAP_TILE_SIZE - MAP_HEIGHT / 2,
  }, zoom);
}

function ownMapNode() {
  const data = state.collaborationMap.data;
  return data?.nodes?.find((node) => node.own) || data?.own || null;
}

function defaultCollaborationMapOrigin(zoom = MAP_ZOOM) {
  return mapOriginForNode(ownMapNode(), zoom);
}

function clampMapOrigin(origin, zoom) {
  const world = mapWorldSize(zoom);
  return {
    x: Math.max(0, Math.min(Math.max(0, world - MAP_WIDTH), origin.x)),
    y: Math.max(0, Math.min(Math.max(0, world - MAP_HEIGHT), origin.y)),
  };
}

function projectMapPoint(longitude, latitude) {
  return mapPoint({ longitude, latitude }, mapExtent());
}

function lonLatToTile(longitude, latitude, zoom) {
  const boundedLatitude = Math.max(-85.0511, Math.min(85.0511, Number(latitude)));
  const latRad = boundedLatitude * Math.PI / 180;
  const scale = 2 ** zoom;
  return {
    x: (Number(longitude) + 180) / 360 * scale,
    y: (1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * scale,
  };
}

function tileToPixel(tile, origin, tileSize = MAP_TILE_SIZE) {
  return {
    x: (tile.x - origin.x) * tileSize,
    y: (tile.y - origin.y) * tileSize,
  };
}

function mapExtent() {
  const zoom = state.collaborationMap.zoom || MAP_ZOOM;
  const origin = clampMapOrigin(state.collaborationMap.origin || defaultMapOrigin(zoom), zoom);
  state.collaborationMap.origin = origin;
  return {
    zoom,
    origin: {
      x: origin.x / MAP_TILE_SIZE,
      y: origin.y / MAP_TILE_SIZE,
    },
    width: MAP_WIDTH,
    height: MAP_HEIGHT,
  };
}

function mapPoint(node, extent) {
  return tileToPixel(lonLatToTile(node.longitude, node.latitude, extent.zoom), extent.origin);
}

function osmTiles(extent) {
  const tileSize = MAP_TILE_SIZE;
  const scale = 2 ** extent.zoom;
  const startX = Math.floor(extent.origin.x);
  const endX = Math.ceil(extent.origin.x + extent.width / tileSize);
  const startY = Math.floor(extent.origin.y);
  const endY = Math.ceil(extent.origin.y + extent.height / tileSize);
  const tiles = [];
  for (let x = startX; x <= endX; x += 1) {
    for (let y = startY; y <= endY; y += 1) {
      if (x < 0 || x >= scale) continue;
      if (y < 0 || y >= scale) continue;
      tiles.push(
        `<image href="https://tile.openstreetmap.org/${extent.zoom}/${x}/${y}.png" x="${((x - extent.origin.x) * tileSize).toFixed(1)}" y="${((y - extent.origin.y) * tileSize).toFixed(1)}" width="${tileSize}" height="${tileSize}" preserveAspectRatio="none"></image>`
      );
    }
  }
  return tiles.join("");
}

function mapPath(points) {
  return points
    .map(([longitude, latitude], index) => {
      const point = projectMapPoint(longitude, latitude);
      return `${index ? "L" : "M"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
    })
    .join(" ") + " Z";
}

function collaborationLandPaths() {
  const shapes = [
    [[-168, 72], [-52, 72], [-56, 50], [-92, 16], [-118, 22], [-125, 48], [-168, 58]],
    [[-82, 13], [-35, 10], [-46, -56], [-72, -54], [-80, -18]],
    [[-18, 72], [44, 70], [35, 36], [-10, 35], [-30, 58]],
    [[-18, 35], [52, 34], [48, -35], [16, -35], [-18, 5]],
    [[35, 70], [178, 68], [150, 8], [94, 8], [70, 28], [42, 35]],
    [[110, -10], [156, -12], [154, -44], [114, -39]],
  ];
  return shapes.map((shape) => `<path d="${mapPath(shape)}"></path>`).join("");
}

function edgePath(source, target) {
  const start = projectMapPoint(source.longitude, source.latitude);
  const end = projectMapPoint(target.longitude, target.latitude);
  const midX = (start.x + end.x) / 2;
  const lift = Math.min(90, Math.max(24, Math.abs(end.x - start.x) * 0.12));
  const midY = Math.min(start.y, end.y) - lift;
  return `M${start.x.toFixed(1)} ${start.y.toFixed(1)} Q${midX.toFixed(1)} ${midY.toFixed(1)} ${end.x.toFixed(1)} ${end.y.toFixed(1)}`;
}

function mapEdgePath(source, target, extent) {
  const start = mapPoint(source, extent);
  const end = mapPoint(target, extent);
  const midX = (start.x + end.x) / 2;
  const lift = Math.min(90, Math.max(22, Math.abs(end.x - start.x) * 0.14));
  const midY = Math.min(start.y, end.y) - lift;
  return `M${start.x.toFixed(1)} ${start.y.toFixed(1)} Q${midX.toFixed(1)} ${midY.toFixed(1)} ${end.x.toFixed(1)} ${end.y.toFixed(1)}`;
}

function mapTooltip(node) {
  if (state.collaborationMap.mode === "citations") {
    const researchers = (node.researchers || []).slice(0, 8)
      .map((item) => `${item.name} (${item.citation_count})`).join(", ");
    const extra = Number(node.researcher_count || 0) > 8 ? `, +${node.researcher_count - 8} more` : "";
    return [
      `<strong>${escapeHtml(node.name)}</strong>`,
      node.country ? `<span>${escapeHtml(node.country)}</span>` : "",
      node.citation_count ? `<span>${node.citation_count} citation link${node.citation_count === 1 ? "" : "s"}</span>` : "",
      researchers ? `<span>${escapeHtml(researchers + extra)}</span>` : "",
    ].filter(Boolean).join("");
  }
  const authors = (node.authors || []).slice(0, 8).join(", ");
  const extra = (node.authors || []).length > 8 ? `, +${node.authors.length - 8} more` : "";
  return [
    `<strong>${escapeHtml(node.name)}</strong>`,
    node.country ? `<span>${escapeHtml(node.country)}</span>` : "",
    node.publication_count ? `<span>${node.publication_count} publication${node.publication_count === 1 ? "" : "s"}</span>` : "",
    authors ? `<span>${escapeHtml(authors + extra)}</span>` : "",
  ].filter(Boolean).join("");
}

async function loadCollaborationMap() {
  if (!hasVitaminePlus()) {
    const container = $("#collaborationMap");
    if (container) {
      container.classList.add("plusMapLocked");
      container.innerHTML = '<button type="button"><strong>Full network map with VitaMine+</strong><span>Upgrade to explore institutions and collaboration details.</span></button>';
      container.querySelector("button")?.addEventListener("click", showWorkspacePlusDialog);
    }
    $("#collaborationMapStats").innerHTML = "";
    $("#collaborationCountries").innerHTML = "";
    return;
  }
  $("#collaborationMap")?.classList.remove("plusMapLocked");
  const mode = state.collaborationMap.mode;
  const data = await api(`/api/collaboration-map?mode=${encodeURIComponent(mode)}`);
  state.collaborationMap.datasets[mode] = data;
  state.collaborationMap.data = data;
  if (!state.collaborationMap.origin) {
    state.collaborationMap.zoom = MAP_ZOOM;
    state.collaborationMap.origin = defaultCollaborationMapOrigin(MAP_ZOOM);
  }
  renderCollaborationMap();
}

function renderCollaborationMap() {
  const data = state.collaborationMap.data;
  if (!data) return;
  const container = $("#collaborationMap");
  const own = data.nodes.find((node) => node.own) || data.own;
  const collaborators = (data.nodes || []).filter((node) => !node.own);
  if (!collaborators.length) {
    const needsOwnInstitution = data.needs_own_institution;
    container.innerHTML = `
      <div class="emptyMap">
        <strong>${needsOwnInstitution ? "Institution mapping pending" : `No ${state.collaborationMap.mode === "citations" ? "citation" : "collaboration"} geography yet`}</strong>
        <span>${needsOwnInstitution ? "Save an institution or connect ORCID; VitaMine will add its map coordinates automatically." : "Run Enrich CV to collect OpenAlex institution locations."}</span>
      </div>`;
    $("#collaborationMapStats").innerHTML = `<span>Institutions: <strong>0</strong></span>`;
    $("#collaborationCountries").innerHTML = "";
    return;
  }

  const nodeById = Object.fromEntries((data.nodes || []).map((node) => [node.id, node]));
  const extent = mapExtent(data.nodes || []);
  const edges = (data.edges || [])
    .map((edge) => ({ ...edge, sourceNode: nodeById[edge.source], targetNode: nodeById[edge.target] }))
    .filter((edge) => edge.sourceNode && edge.targetNode);
  const edgeSvg = edges
    .map((edge) => {
      const width = Math.min(5, 0.8 + Number(edge.weight || 1) * 0.45);
      return `<path class="collabEdge" d="${mapEdgePath(edge.sourceNode, edge.targetNode, extent)}" stroke-width="${width.toFixed(1)}">
        <title>${escapeHtml(edge.targetNode.name)}: ${edge.weight} publication links</title>
      </path>`;
    })
    .join("");
  const nodeMarkers = collaborators
    .map((node) => {
      const point = mapPoint(node, extent);
      const radius = Math.min(12, 3.5 + Math.sqrt(Number(node.citation_count || node.publication_count || 1)) * 2);
      return `<button class="mapMarker collabMarker" type="button" style="left:${(point.x / MAP_WIDTH * 100).toFixed(3)}%;top:${(point.y / MAP_HEIGHT * 100).toFixed(3)}%;width:${(radius * 2).toFixed(1)}px;height:${(radius * 2).toFixed(1)}px" aria-label="${escapeHtml(node.name)}">
        <span class="mapTooltip">${mapTooltip(node)}</span>
      </button>`;
    })
    .join("");
  const ownPoint = mapPoint(own, extent);
  container.innerHTML = `
    <svg viewBox="0 0 1000 520" preserveAspectRatio="none" class="osmTiles" aria-hidden="true">
      ${osmTiles(extent)}
    </svg>
    <svg viewBox="0 0 1000 520" preserveAspectRatio="none" class="collaborationSvg mapFallback" aria-hidden="true">
      <rect class="mapOcean" x="0" y="0" width="1000" height="520"></rect>
      <g class="mapLand">${collaborationLandPaths()}</g>
    </svg>
    <svg viewBox="0 0 1000 520" preserveAspectRatio="none" class="collaborationOverlay" aria-hidden="true">
      ${edgeSvg}
    </svg>`;
  container.insertAdjacentHTML(
    "beforeend",
    `${nodeMarkers}
    <button class="mapMarker ownMarker" type="button" style="left:${(ownPoint.x / MAP_WIDTH * 100).toFixed(3)}%;top:${(ownPoint.y / MAP_HEIGHT * 100).toFixed(3)}%" aria-label="${escapeHtml(own.name)}">
      <span class="mapTooltip"><strong>${escapeHtml(own.name)}</strong><span>${escapeHtml(own.country || "")}</span></span>
    </button>
    <div class="mapControls" aria-label="Map controls">
      <button type="button" data-map-zoom="in" aria-label="Zoom in">+</button>
      <button type="button" data-map-zoom="out" aria-label="Zoom out">-</button>
      <button type="button" data-map-zoom="reset" aria-label="Reset map">Reset</button>
    </div>
    <a class="osmCredit" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap</a>`
  );
  bindCollaborationMapControls(container);
  $("#collaborationMapStats").innerHTML = [
    `<span>Institutions: <strong>${data.institution_count}</strong></span>`,
    state.collaborationMap.mode === "citations"
      ? `<span>Researchers: <strong>${data.researcher_count}</strong></span><span>Citation links: <strong>${data.citation_links}</strong></span>`
      : `<span>Publication links: <strong>${data.publication_links}</strong></span>`,
  ].join("");
  $("#collaborationCountries").innerHTML = (data.top_countries || [])
    .map((row) => `<span>${escapeHtml(row.country)}: <strong>${row.citation_count ?? row.publication_count}</strong></span>`)
    .join("");
}

function mapViewPoint(event, container) {
  const rect = container.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) / rect.width * MAP_WIDTH,
    y: (event.clientY - rect.top) / rect.height * MAP_HEIGHT,
  };
}

function zoomCollaborationMap(direction, focal = { x: MAP_WIDTH / 2, y: MAP_HEIGHT / 2 }) {
  const oldZoom = state.collaborationMap.zoom || MAP_ZOOM;
  const nextZoom = Math.max(MAP_MIN_ZOOM, Math.min(MAP_MAX_ZOOM, oldZoom + direction));
  if (nextZoom === oldZoom) return;
  const oldOrigin = state.collaborationMap.origin || defaultCollaborationMapOrigin(oldZoom);
  const scale = mapWorldSize(nextZoom) / mapWorldSize(oldZoom);
  state.collaborationMap.zoom = nextZoom;
  state.collaborationMap.origin = clampMapOrigin(
    {
      x: (oldOrigin.x + focal.x) * scale - focal.x,
      y: (oldOrigin.y + focal.y) * scale - focal.y,
    },
    nextZoom
  );
  renderCollaborationMap();
}

function resetCollaborationMap() {
  state.collaborationMap.zoom = MAP_ZOOM;
  state.collaborationMap.origin = defaultCollaborationMapOrigin(MAP_ZOOM);
  renderCollaborationMap();
}

function bindCollaborationMapControls(container) {
  if (container.dataset.mapEventsBound) return;
  container.dataset.mapEventsBound = "true";
  container.addEventListener("click", (event) => {
    const button = event.target.closest("[data-map-zoom]");
    if (!button) return;
    const action = button.dataset.mapZoom;
    if (action === "in") zoomCollaborationMap(1);
    if (action === "out") zoomCollaborationMap(-1);
    if (action === "reset") resetCollaborationMap();
  });
  container.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomCollaborationMap(event.deltaY < 0 ? 1 : -1, mapViewPoint(event, container));
  }, { passive: false });
  container.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".mapMarker, .mapControls, .osmCredit")) return;
    container.setPointerCapture(event.pointerId);
    state.collaborationMap.drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: { ...(state.collaborationMap.origin || defaultCollaborationMapOrigin(state.collaborationMap.zoom || MAP_ZOOM)) },
    };
    container.classList.add("dragging");
  });
  container.addEventListener("pointermove", (event) => {
    const drag = state.collaborationMap.drag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const rect = container.getBoundingClientRect();
    const dx = (event.clientX - drag.startX) / rect.width * MAP_WIDTH;
    const dy = (event.clientY - drag.startY) / rect.height * MAP_HEIGHT;
    const zoom = state.collaborationMap.zoom || MAP_ZOOM;
    state.collaborationMap.origin = clampMapOrigin({ x: drag.origin.x - dx, y: drag.origin.y - dy }, zoom);
    renderCollaborationMap();
  });
  const stopDrag = (event) => {
    const drag = state.collaborationMap.drag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    state.collaborationMap.drag = null;
    container.classList.remove("dragging");
  };
  container.addEventListener("pointerup", stopDrag);
  container.addEventListener("pointercancel", stopDrag);
}

async function selectDashboardMapMode(mode) {
  if (!["collaborations", "citations"].includes(mode) || mode === state.collaborationMap.mode) return;
  state.collaborationMap.mode = mode;
  document.querySelectorAll("[data-dashboard-map-mode]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.dashboardMapMode === mode));
  });
  state.collaborationMap.data = state.collaborationMap.datasets[mode] || null;
  if (state.collaborationMap.data) renderCollaborationMap();
  else await loadCollaborationMap();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function actionLog(target, data) {
  const lines = [];
  if (data.stdout) {
    try {
      const parsed = JSON.parse(data.stdout);
      Object.entries(parsed).forEach(([key, value]) => lines.push(`${key}: ${value}`));
    } catch (_error) {
      lines.push(data.stdout.trim());
    }
  }
  Object.entries(data)
    .filter(([key]) => !["ok", "stdout"].includes(key))
    .forEach(([key, value]) => lines.push(`${key}: ${value}`));
  appendConsole(lines.filter(Boolean).join("\n") || "Done.");
}

function exportLanguage() {
  return $("#exportLanguage")?.value || "en";
}

async function loadExportSettings() {
  const data = await api("/api/export-settings");
  state.exportSettings = data;
  const label = data.home_language_label || "Deutsch";
  const code = data.home_language_code || "de";
  $("#homeLanguageLabel").value = label;
  $("#homeLanguageCode").value = code;
  $("#homeLanguageOption").textContent = label;
  $("#additionalLanguageLegend").textContent = label;
  $("#translateEntryToAdditional").textContent = `Translate English → ${label}`;
  $("#translateEntryToEnglish").textContent = `Translate ${label} → English`;
  const citationStyle = $("#exportCitationStyle");
  if (citationStyle) {
    const styleGroups = new Map();
    (data.citation_style_options || []).forEach((option) => {
      const group = option.group || "Other";
      if (!styleGroups.has(group)) styleGroups.set(group, []);
      styleGroups.get(group).push(option);
    });
    citationStyle.innerHTML = [...styleGroups.entries()].map(([group, options]) =>
      `<optgroup label="${escapeHtml(group)}">${options.map((option) =>
        `<option value="${escapeHtml(option.id)}" title="${escapeHtml(option.description || "")}">${escapeHtml(option.label)}</option>`
      ).join("")}</optgroup>`
    ).join("");
    citationStyle.value = data.citation_style || "vitamine-long";
  }
  renderLongPublicationCategories(data);
}

async function loadExportFormats() {
  try {
    const data = await api("/api/export-formats");
    state.exportFormatsApiAvailable = true;
    state.exportFormats = Array.isArray(data.formats) ? data.formats : [];
  } catch (error) {
    if (error.status !== 404) throw error;
    const response = await fetch("/static/export-formats.json");
    if (!response.ok) throw error;
    const data = await response.json();
    state.exportFormatsApiAvailable = false;
    state.exportFormats = (Array.isArray(data.formats) ? data.formats : []).map((format) => ({
      ...format,
      installed: Boolean(format.preinstalled),
    }));
  }
  renderExportFormats();
  renderPromptExportFormats();
}

function promptCapableFormats() {
  return state.exportFormats.filter((format) => !format.custom_template && format.installed && ["long", "short", "ultrashort"].includes(format.exporter));
}

function customTemplateDefaultName(filename) {
  return String(filename || "My Word CV")
    .replace(/\.docx$/i, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim() || "My Word CV";
}

async function importCustomExportTemplate(file) {
  if (!file) return;
  if (!/\.docx$/i.test(file.name || "")) {
    setStatus("Please choose a Word .docx document.", { error: true });
    return;
  }
  if (file.size > 20 * 1024 * 1024) {
    setStatus("The Word template is larger than 20 MB.", { error: true });
    return;
  }
  state.pendingCustomTemplateFile = file;
  const nameInput = $("#customTemplateName");
  if (nameInput && !nameInput.value.trim()) nameInput.value = customTemplateDefaultName(file.name);
  $("#customTemplateFileLabel").textContent = file.name;
  const form = new FormData();
  form.append("file", file);
  form.append("name", nameInput?.value.trim() || customTemplateDefaultName(file.name));
  const stopProcessing = startProcessing(
    "Learning the Word CV format…",
    "VitaMine is mapping headings, classifying the CV, and creating private layout placeholders.",
  );
  setActionButtons(true);
  $("#customTemplateStatus").textContent = `Analyzing ${file.name}…`;
  try {
    const data = await api("/api/export-templates", { method: "POST", body: form });
    const format = data.format || {};
    const classification = format.content_profile_label || "CV";
    const mapped = format.template_analysis?.mapped_sections?.length || 0;
    $("#customTemplateStatus").textContent = `${format.name || "Word template"} added as ${classification}; ${mapped} section${mapped === 1 ? "" : "s"} mapped.`;
    state.pendingCustomTemplateFile = null;
    $("#customTemplateFileInput").value = "";
    $("#customTemplateFileLabel").textContent = "DOCX only · up to 20 MB";
    await loadExportFormats();
    setStatus(`${format.name || "Word template"} added to Your formats`);
  } catch (error) {
    $("#customTemplateStatus").textContent = error.message || "The Word template could not be added.";
    setStatus(error.message, { error: true });
  } finally {
    stopProcessing();
    setActionButtons(false);
  }
}

async function renameCustomExportTemplate(formatId) {
  const format = state.exportFormats.find((item) => item.id === formatId && item.custom_template);
  if (!format) return;
  const name = window.prompt("Template name", format.name);
  if (name === null || !name.trim() || name.trim() === format.name) return;
  await api(`/api/export-templates/${encodeURIComponent(formatId)}`, {
    method: "PUT",
    body: JSON.stringify({ name: name.trim() }),
  });
  await loadExportFormats();
  setStatus("Word template renamed");
}

async function deleteCustomExportTemplate(formatId) {
  const format = state.exportFormats.find((item) => item.id === formatId && item.custom_template);
  if (!format || !window.confirm(`Delete the private Word template “${format.name}”?`)) return;
  await api(`/api/export-templates/${encodeURIComponent(formatId)}`, { method: "DELETE" });
  delete state.exportArtifacts[formatId];
  await loadExportFormats();
  setStatus("Word template deleted");
}

function renderPromptExportFormats() {
  const select = $("#promptExportFormat");
  if (!select) return;
  const previous = select.value;
  const formats = promptCapableFormats();
  select.innerHTML = formats.map((format) => `<option value="${escapeHtml(format.id)}">${escapeHtml(format.name)}</option>`).join("");
  if (formats.some((format) => format.id === previous)) select.value = previous;
  loadPromptExportPlan();
}

function renderPromptExportPlan(plan) {
  const container = $("#promptExportPlan");
  const clear = $("#clearPromptExportPlan");
  if (!container) return;
  if (!plan) {
    container.hidden = true;
    container.innerHTML = "";
    if (clear) clear.hidden = true;
    return;
  }
  const papers = (plan.selected_publications || []).map((publication, index) => `
    <li>
      <strong>${index + 1}. ${escapeHtml(publication.title || "Untitled publication")}</strong>
      <span>${escapeHtml([publication.year, publication.venue, publication.authorship?.replace("_", "/"), publication.impact_factor != null ? `IF ${publication.impact_factor}` : ""].filter(Boolean).join(" · "))}</span>
    </li>`).join("");
  const warnings = (plan.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  container.innerHTML = `
    <div class="promptPlanSummary">
      <strong>${escapeHtml(plan.interpretation || "Export plan ready")}</strong>
      <span>${plan.max_pages ? `Target: ${plan.max_pages} page${plan.max_pages === 1 ? "" : "s"} · ` : ""}${plan.selected_publication_ids?.length || 0} publication${plan.selected_publication_ids?.length === 1 ? "" : "s"} · ${escapeHtml((plan.section_strategy || "compact").replace("_", " "))}</span>
    </div>
    ${warnings ? `<ul class="promptPlanWarnings">${warnings}</ul>` : ""}
    <ol class="promptPlanPublications">${papers}</ol>`;
  container.hidden = false;
  if (clear) clear.hidden = false;
}

async function loadPromptExportPlan() {
  const formatId = $("#promptExportFormat")?.value;
  if (!formatId || !state.exportFormatsApiAvailable) {
    renderPromptExportPlan(null);
    return;
  }
  try {
    const data = await api(`/api/export-formats/${encodeURIComponent(formatId)}/prompt-plan`);
    state.exportPromptPlans[formatId] = data.plan || null;
    renderPromptExportPlan(data.plan);
    if (data.plan?.prompt && $("#promptExportInstructions")) $("#promptExportInstructions").value = data.plan.prompt;
  } catch (error) {
    renderPromptExportPlan(null);
    setStatus(error.message, { error: true });
  }
}

async function createPromptExportPlan() {
  const formatId = $("#promptExportFormat")?.value;
  const prompt = $("#promptExportInstructions")?.value.trim();
  if (!formatId || !prompt) {
    setStatus("Choose a format and describe the export you want.");
    return;
  }
  const stopProcessing = startProcessing("Creating a tailored export plan…", "The configured LLM is reviewing publication metadata and your constraints.");
  setActionButtons(true);
  try {
    const data = await api(`/api/export-formats/${encodeURIComponent(formatId)}/prompt-plan`, {
      method: "POST",
      body: JSON.stringify({ prompt }),
    });
    state.exportPromptPlans[formatId] = data.plan;
    renderPromptExportPlan(data.plan);
    renderExportFormats();
    setStatus(`Export plan ready with ${data.plan?.selected_publication_ids?.length || 0} publications`);
    appendConsole(JSON.stringify(data.plan, null, 2));
  } catch (error) {
    setStatus(error.message, { error: true });
  } finally {
    stopProcessing();
    setActionButtons(false);
  }
}

async function clearPromptExportPlan() {
  const formatId = $("#promptExportFormat")?.value;
  if (!formatId) return;
  await api(`/api/export-formats/${encodeURIComponent(formatId)}/prompt-plan`, { method: "DELETE" });
  state.exportPromptPlans[formatId] = null;
  renderPromptExportPlan(null);
  setStatus("Prompt-guided export plan cleared");
}

function renderLongPublicationCategories(settings = {}) {
  const containers = $$(".longPublicationCategories");
  if (!containers.length) return;
  const options = settings.long_cv_publication_category_options || [];
  const selected = new Set(settings.long_cv_publication_categories || []);
  const markup = options.map((option) => `<label>
    <input type="checkbox" class="longPublicationCategory" value="${escapeHtml(option.key)}" ${selected.has(option.key) ? "checked" : ""}>
    ${escapeHtml(option.label)}
  </label>`).join("");
  containers.forEach((container) => { container.innerHTML = markup; });
  $$(".longPublicationCategory").forEach((input) => {
    input.addEventListener("change", () => {
      state.exportSettings.long_cv_publication_categories = selectedLongPublicationCategories();
    });
  });
}

function selectedLongPublicationCategories() {
  return $$(".longPublicationCategory:checked").map((input) => input.value);
}

async function saveExportSettings() {
  const label = $("#homeLanguageLabel").value.trim() || "Deutsch";
  const code = $("#homeLanguageCode").value.trim().toLowerCase() || "de";
  const categories = selectedLongPublicationCategories();
  $("#homeLanguageLabel").value = label;
  $("#homeLanguageCode").value = code;
  $("#homeLanguageOption").textContent = label;
  $("#additionalLanguageLegend").textContent = label;
  $("#translateEntryToAdditional").textContent = `Translate English → ${label}`;
  $("#translateEntryToEnglish").textContent = `Translate ${label} → English`;
  state.exportSettings.home_language_label = label;
  state.exportSettings.home_language_code = code;
  state.exportSettings.long_cv_publication_categories = categories;
  state.exportSettings.citation_style = $("#exportCitationStyle")?.value || "vitamine-long";
  await api("/api/export-settings", {
    method: "PUT",
    body: JSON.stringify({
      home_language_label: label,
      home_language_code: code,
      citation_style: state.exportSettings.citation_style,
      long_cv_publication_categories: categories,
    }),
  });
  setStatus("Native / second CV language saved");
}

function exportFormatMatches(format, query) {
  if (!query) return true;
  const searchable = [
    format.name,
    format.summary,
    format.length,
    format.content_profile_label,
    format.audience,
    ...(format.focus || []),
  ].join(" ").toLowerCase();
  return searchable.includes(query);
}

function exportFormatCard(format) {
  const quality = format.quality || {};
  const source = format.source || {};
  const artifact = state.exportArtifacts[format.id] || {};
  const focus = (format.focus || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
  const profileLabel = format.content_profile_label || ({
    long: "Long CV",
    short: "Short CV",
    one_page: "One-page CV",
    biosketch: "Biosketch",
  }[format.content_profile] || "");
  const sourceLink = String(source.url || "").startsWith("https://")
    ? `<a class="formatSourceLink" href="${escapeHtml(source.url)}" target="_blank" rel="noopener">Format guidance</a>`
    : "";
  let actions = "";
  if (format.installed) {
    const buildButton = format.exporter
      ? `<button class="formatActionButton formatBuildButton" data-format-id="${escapeHtml(format.id)}" type="button">Export CV in this format</button>`
      : `<button type="button" disabled title="The Word exporter has not been implemented yet">Export coming later</button>`;
    const artifactLink = artifact.docx && !state.cloud.enabled
      ? `<a href="${escapeHtml(artifact.docx)}" title="${escapeHtml(artifact.docx_path || "")}" target="_blank" rel="noopener">Open last export</a>`
      : "";
    const manageButton = format.custom_template
      ? `<button class="formatActionButton quietButton formatRenameTemplateButton" data-format-id="${escapeHtml(format.id)}" type="button">Rename</button><button class="formatActionButton quietButton formatDeleteTemplateButton" data-format-id="${escapeHtml(format.id)}" type="button">Delete</button>`
      : `<button class="formatActionButton quietButton formatRemoveButton" data-format-id="${escapeHtml(format.id)}" type="button">Remove</button>`;
    actions = `${buildButton}${artifactLink}${manageButton}`;
  } else {
    actions = `<button class="formatActionButton formatInstallButton" data-format-id="${escapeHtml(format.id)}" type="button">Add format</button>`;
  }
  const longOptions = format.installed && format.content_profile === "long"
    ? `<details class="formatCardOptions">
        <summary>Content options</summary>
        <div class="checkboxStack compact longPublicationCategories"></div>
      </details>`
    : "";
  const preview = format.custom_template
    ? `<div class="customWordPreview" aria-hidden="true"><span>W</span><small>${escapeHtml(format.content_profile_label || "CV")}</small></div>`
    : `<img src="${escapeHtml(format.preview)}" alt="">`;
  const analysis = format.custom_template && format.template_analysis
    ? `<span class="templateAnalysisNote">${format.template_analysis.page_count ? `${format.template_analysis.page_count} source page${format.template_analysis.page_count === 1 ? "" : "s"} · ` : ""}${format.template_analysis.mapped_sections?.length || 0} mapped sections${format.template_analysis.model ? ` · analyzed with ${escapeHtml(format.template_analysis.model)}` : ""}</span>`
    : "";
  const qualityMark = quality.key === "ready"
    ? '<span class="qualityReadyIcon" aria-label="Ready" title="Ready">✓</span>'
    : `<span class="qualityBadge quality-${escapeHtml(quality.key || "preview")}" title="${escapeHtml(quality.description || "")}">${escapeHtml(quality.label || "")}</span>`;
  const pageLimit = Number(format.page_limit) > 0
    ? `<span class="pageLimitBadge" title="VitaMine automatically selects high-yield records to stay within this limit">Fixed limit: ${Number(format.page_limit)} pages</span>`
    : "";
  return `<article class="formatCard ${format.installed ? "installed" : ""}">
    <div class="formatPreview">
      ${preview}
    </div>
    <div class="formatCardBody">
      <div class="formatTitleRow">
        <div>
          <h4>${escapeHtml(format.name)}</h4>
          ${qualityMark}
        </div>
        ${format.preinstalled ? '<span class="defaultBadge">Included</span>' : ""}
      </div>
      <span class="formatLength">${escapeHtml(format.length || "")}</span>
      ${pageLimit}
      <span class="contentProfileBadge" title="${escapeHtml(format.content_profile_description || "Controls which CV content-selection routine is used.")}">${escapeHtml(profileLabel)} content</span>
      <p>${escapeHtml(format.summary || "")}</p>
      ${analysis}
      <div class="formatFocus" aria-label="Focus">${focus}</div>
      ${longOptions}
      ${sourceLink ? `<div class="formatSource">${sourceLink}</div>` : ""}
      <div class="buttonRow formatActions">${actions}</div>
    </div>
  </article>`;
}

function bindExportFormatActions() {
  $$(".formatInstallButton").forEach((button) => {
    button.addEventListener("click", () => installExportFormat(button.dataset.formatId));
  });
  $$(".formatRemoveButton").forEach((button) => {
    button.addEventListener("click", () => removeExportFormat(button.dataset.formatId));
  });
  $$(".formatBuildButton").forEach((button) => {
    button.addEventListener("click", () => buildExportFormat(button.dataset.formatId));
  });
  $$(".formatRenameTemplateButton").forEach((button) => {
    button.addEventListener("click", () => renameCustomExportTemplate(button.dataset.formatId));
  });
  $$(".formatDeleteTemplateButton").forEach((button) => {
    button.addEventListener("click", () => deleteCustomExportTemplate(button.dataset.formatId));
  });
}

function renderExportFormats() {
  const query = ($("#exportFormatSearch")?.value || "").trim().toLowerCase();
  const matching = state.exportFormats.filter((format) => exportFormatMatches(format, query));
  const installed = matching.filter((format) => format.installed);
  const available = matching.filter((format) => !format.installed);
  const installedContainer = $("#installedExportFormats");
  const availableContainer = $("#availableExportFormats");
  if (installedContainer) installedContainer.innerHTML = installed.map(exportFormatCard).join("");
  if (availableContainer) availableContainer.innerHTML = available.map(exportFormatCard).join("");
  if ($("#availableFormatsEmpty")) $("#availableFormatsEmpty").hidden = available.length > 0;
  if ($("#exportFormatCount")) {
    const installedTotal = state.exportFormats.filter((format) => format.installed).length;
    $("#exportFormatCount").textContent = `${installedTotal} format${installedTotal === 1 ? "" : "s"}`;
  }
  renderLongPublicationCategories(state.exportSettings);
  bindExportFormatActions();
}

async function installExportFormat(formatId) {
  if (!state.exportFormatsApiAvailable) {
    const format = state.exportFormats.find((item) => item.id === formatId);
    if (format) format.installed = true;
    renderExportFormats();
    setStatus("Format added for this session");
    return;
  }
  await api(`/api/export-formats/${encodeURIComponent(formatId)}/install`, { method: "POST" });
  await loadExportFormats();
  setStatus("Format added");
}

async function removeExportFormat(formatId) {
  if (!state.exportFormatsApiAvailable) {
    const format = state.exportFormats.find((item) => item.id === formatId);
    if (format) format.installed = false;
    delete state.exportArtifacts[formatId];
    renderExportFormats();
    setStatus("Format removed for this session");
    return;
  }
  await api(`/api/export-formats/${encodeURIComponent(formatId)}/install`, { method: "DELETE" });
  delete state.exportArtifacts[formatId];
  await loadExportFormats();
  setStatus("Format removed");
}

async function buildExportFormat(formatId) {
  const format = state.exportFormats.find((item) => item.id === formatId);
  if (!format) return;
  const language = exportLanguage();
  if (format.content_profile === "long") await saveExportSettings();
  const legacyPaths = {
    one_page: "/api/actions/build-ultrashort-tabular",
    short: "/api/actions/build-short",
    long: "/api/actions/build-long",
    biosketch: "/api/actions/build-biosketch",
  };
  const actionPath = state.exportFormatsApiAvailable
    ? `/api/actions/export/${encodeURIComponent(formatId)}`
    : legacyPaths[format.content_profile];
  if (!actionPath) return;
  $("#exportResult").hidden = false;
  $("#exportResultTitle").textContent = `Creating ${format.name}…`;
  $("#exportOutput").textContent = state.cloud.enabled
    ? "The Word document will download when it is ready."
    : "The Word document will open when it is ready.";
  $("#exportResultLink").hidden = true;
  let data;
  try {
    data = await runAction(
      `${actionPath}?lang=${encodeURIComponent(language)}`,
      `${format.name} exported`,
      `Creating ${format.name}...`,
    );
  } catch (error) {
    $("#exportResultTitle").textContent = `Could not export ${format.name}`;
    $("#exportOutput").textContent = error.message || "Export failed";
    return;
  }
  state.exportArtifacts[formatId] = data;
  renderExportFormats();
  $("#exportResultTitle").textContent = `${format.name} is ready`;
  const audit = data.quality_audit;
  const auditNote = audit
    ? audit.status === "failed"
      ? " Final quality check could not run."
      : ` Final quality check: ${audit.applied_count || 0} safe correction${audit.applied_count === 1 ? "" : "s"} applied${audit.review_count ? `; ${audit.review_count} suggestion${audit.review_count === 1 ? "" : "s"} flagged for review` : ""}.${audit.status === "deterministic_only" ? " The LLM audit was unavailable." : ""}`
    : "";
  const reviewDetails = (audit?.issues || [])
    .filter((issue) => !issue.applied)
    .slice(0, 3)
    .map((issue) => issue.reason)
    .filter(Boolean)
    .join("; ");
  $("#exportOutput").textContent = `${data.docx_path ? data.docx_path.split("/").pop() : "Word document created"}.${auditNote}${reviewDetails ? ` Review: ${reviewDetails}` : ""}`;
  $("#exportResultLink").href = data.docx;
  $("#exportResultLink").hidden = state.cloud.enabled;
  openBuiltArtifact(data);
}

function openBuiltArtifact(data) {
  const href = data.docx;
  if (!href) return;
  if (state.cloud.enabled) {
    const download = document.createElement("a");
    download.href = href;
    download.download = data.docx_path?.split("/").pop() || "vitamine-export.docx";
    download.hidden = true;
    document.body.appendChild(download);
    download.click();
    download.remove();
    return;
  }
  window.open(href, "_blank", "noopener,width=980,height=760,left=0,top=0");
}

function summaryBox(title, lines) {
  return `<div class="summaryBox"><h2>${title}</h2>${lines.map((line) => `<p>${line}</p>`).join("")}</div>`;
}

async function loadEntries() {
  const params = new URLSearchParams();
  const section = $("#sectionFilter").value;
  const q = $("#entrySearch").value.trim();
  if (section) params.set("section", section);
  if (q) params.set("q", q);
  const data = await api(`/api/entries?${params.toString()}`);
  state.entries = data.entries;
  renderEntries();
}

function renderEntries() {
  const showSectionHeadings = !$("#sectionFilter").value;
  const rows = [];
  let lastSection = null;
  state.entries.forEach((entry) => {
    if (showSectionHeadings && entry.section_key !== lastSection) {
      lastSection = entry.section_key;
      rows.push(`<tr class="sectionRow"><td colspan="4">${state.sections[entry.section_key] || entry.section_key}</td></tr>`);
    }
    const selected = state.selectedEntry && state.selectedEntry.id === entry.id ? " selected" : "";
    const dates = [entry.start_date || "", entry.end_date || ""].filter(Boolean).join("-");
    rows.push(`<tr class="${selected}" data-id="${entry.id}">
        <td>${state.sections[entry.section_key] || entry.section_key}</td>
        <td>${dates}</td>
        <td>${entry.section_key === "funding" && entry.grant_status ? `<span class="grantStatus grantStatus-${entry.grant_status}">${entry.grant_status}</span>` : ""}${entry.title || ""}</td>
        <td>${entry.organization || ""}</td>
      </tr>`);
  });
  $("#entriesBody").innerHTML = rows.join("");
  $$("#entriesBody tr[data-id]").forEach((row) => {
    row.addEventListener("click", () => selectEntry(Number(row.dataset.id)));
  });
}

function selectEntry(id) {
  const entry = state.entries.find((item) => item.id === id);
  state.selectedEntry = entry;
  $("#entryId").value = entry.id;
  $("#entrySection").value = entry.section_key || "honors";
  $("#entryStart").value = entry.start_date || "";
  $("#entryEnd").value = entry.end_date || "";
  $("#entryGrantStatus").value = entry.grant_status || "funded";
  updateGrantStatusVisibility();
  $("#entryTitle").value = entry.title || "";
  $("#entryOrganization").value = entry.organization || "";
  $("#entryLocation").value = entry.location || "";
  $("#entryRole").value = entry.role || "";
  $("#entryAmount").value = entry.amount || "";
  $("#entryDescription").value = entry.description || "";
  $("#entryTitleDe").value = entry.title_de || "";
  $("#entryOrganizationDe").value = entry.organization_de || "";
  $("#entryLocationDe").value = entry.location_de || "";
  $("#entryRoleDe").value = entry.role_de || "";
  $("#entryAmountDe").value = entry.amount_de || "";
  $("#entryDescriptionDe").value = entry.description_de || "";
  renderAchievements(entry.achievements || []);
  $("#includeExtended").checked = Boolean(entry.include_extended);
  $("#includeLong").checked = Boolean(entry.include_long);
  $("#includeShort").checked = Boolean(entry.include_short);
  $("#includeBiosketch").checked = Boolean(entry.include_biosketch);
  renderEntries();
}

function clearEntryForm() {
  state.selectedEntry = null;
  $("#entryForm").reset();
  $("#entryId").value = "";
  $("#entrySection").value = $("#sectionFilter").value || "honors";
  $("#entryGrantStatus").value = "funded";
  updateGrantStatusVisibility();
  $("#includeExtended").checked = true;
  $("#includeLong").checked = true;
  $("#includeShort").checked = false;
  $("#includeBiosketch").checked = false;
  renderAchievements([]);
  renderEntries();
}

function renderAchievements(achievements) {
  const container = $("#entryAchievements");
  if (!achievements.length) {
    container.innerHTML = "<p>No achievement records linked to this entry yet.</p>";
    return;
  }
  container.innerHTML = achievements
    .map((achievement) => {
      const meta = [achievement.organization, achievement.amount, achievement.year].filter(Boolean).join(" · ");
      return `<div class="achievementItem">
        <strong>${achievement.title || ""}</strong>
        <span>${meta}</span>
      </div>`;
    })
    .join("");
}

function entryPayload() {
  return {
    section_key: $("#entrySection").value,
    grant_status: $("#entrySection").value === "funding" ? $("#entryGrantStatus").value : null,
    start_date: $("#entryStart").value,
    end_date: $("#entryEnd").value,
    title: $("#entryTitle").value,
    title_de: $("#entryTitleDe").value,
    organization: $("#entryOrganization").value,
    organization_de: $("#entryOrganizationDe").value,
    location: $("#entryLocation").value,
    location_de: $("#entryLocationDe").value,
    role: $("#entryRole").value,
    role_de: $("#entryRoleDe").value,
    amount: $("#entryAmount").value,
    amount_de: $("#entryAmountDe").value,
    description: $("#entryDescription").value,
    description_de: $("#entryDescriptionDe").value,
    raw_text: $("#entryDescription").value || $("#entryTitle").value,
    raw_text_de: $("#entryDescriptionDe").value || $("#entryTitleDe").value,
    confidence: "manual",
    language: "en",
    include_extended: $("#includeExtended").checked,
    include_long: $("#includeLong").checked,
    include_short: $("#includeShort").checked,
    include_biosketch: $("#includeBiosketch").checked,
  };
}

function fundingEndHasPassed(value, now = new Date()) {
  const text = String(value || "").trim();
  if (!text || /present|current|ongoing/i.test(text)) return false;
  let end = null;
  let match = text.match(/^(\d{4})$/);
  if (match) end = new Date(Number(match[1]), 11, 31, 23, 59, 59, 999);
  match = match || text.match(/^(\d{1,2})[/.](\d{4})$/);
  if (!end && match && match.length === 3) end = new Date(Number(match[2]), Number(match[1]), 0, 23, 59, 59, 999);
  match = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (!end && match) end = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 23, 59, 59, 999);
  match = text.match(/^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$/);
  if (!end && match) end = new Date(Number(match[3]), Number(match[1]) - 1, Number(match[2]), 23, 59, 59, 999);
  return Boolean(end && !Number.isNaN(end.getTime()) && end < now);
}

function updateGrantStatusVisibility() {
  const isFunding = $("#entrySection").value === "funding";
  const status = $("#entryGrantStatus");
  const expired = isFunding && fundingEndHasPassed($("#entryEnd").value);
  $("#entryGrantStatusField").hidden = !isFunding;
  [...status.options].forEach((option) => {
    option.disabled = expired && option.value !== "past";
  });
  if (expired) status.value = "past";
}

function mergeSavedEntry(id, payload) {
  const numericId = Number(id);
  const index = state.entries.findIndex((entry) => entry.id === numericId);
  const savedEntry = { ...(index >= 0 ? state.entries[index] : {}), ...payload, id: numericId };
  if (index >= 0) {
    state.entries[index] = savedEntry;
  } else {
    state.entries.push(savedEntry);
  }
  if (state.selectedEntry && state.selectedEntry.id === numericId) state.selectedEntry = savedEntry;
  renderEntries();
}

async function persistEntrySnapshot(snapshot) {
  const id = snapshot.id;
  const method = id ? "PUT" : "POST";
  const path = id ? `/api/entries/${id}` : "/api/entries";
  const data = await api(path, { method, body: JSON.stringify(snapshot.payload) });
  const savedId = id || data.id;
  if (savedId) {
    if (!id && $("#entryId").value === "") {
      $("#entryId").value = savedId;
      state.selectedEntry = { ...snapshot.payload, id: Number(savedId), achievements: [] };
    }
    if (!id && state.entryAutosave.pending && !state.entryAutosave.pending.id) {
      state.entryAutosave.pending.id = String(savedId);
    }
    mergeSavedEntry(savedId, data.entry || snapshot.payload);
  }
  setStatus("Entry autosaved");
  await loadSummary();
}

async function runEntryAutosave() {
  clearTimeout(state.entryAutosave.timer);
  state.entryAutosave.timer = null;
  if (state.entryAutosave.saving || !state.entryAutosave.pending) return;

  const snapshot = state.entryAutosave.pending;
  state.entryAutosave.pending = null;
  state.entryAutosave.saving = true;
  try {
    await persistEntrySnapshot(snapshot);
  } catch (error) {
    state.entryAutosave.pending = snapshot;
    setStatus(error.message);
  } finally {
    state.entryAutosave.saving = false;
    if (state.entryAutosave.pending) {
      state.entryAutosave.timer = setTimeout(runEntryAutosave, 500);
    }
  }
}

function scheduleEntryAutosave() {
  updateGrantStatusVisibility();
  state.entryAutosave.pending = {
    id: $("#entryId").value,
    payload: entryPayload(),
  };
  clearTimeout(state.entryAutosave.timer);
  state.entryAutosave.timer = setTimeout(runEntryAutosave, 450);
}

async function saveEntry(event) {
  event.preventDefault();
  state.entryAutosave.pending = {
    id: $("#entryId").value,
    payload: entryPayload(),
  };
  await runEntryAutosave();
}

async function translateSelectedEntry(direction) {
  if (state.entryAutosave.pending) await runEntryAutosave();
  const id = $("#entryId").value;
  if (!id) {
    setStatus("Save the entry before translating it.");
    return;
  }
  const buttons = [$("#translateEntryToAdditional"), $("#translateEntryToEnglish")];
  buttons.forEach((button) => { button.disabled = true; });
  setStatus("Translating entry…");
  try {
    const data = await api(`/api/entries/${id}/translate`, {
      method: "POST",
      body: JSON.stringify({ direction }),
    });
    mergeSavedEntry(id, data.entry);
    selectEntry(Number(id));
    setStatus(`Entry translated from ${data.source_language} to ${data.target_language}`);
  } catch (error) {
    setStatus(error.message);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function deleteEntry() {
  const id = $("#entryId").value;
  if (!id) return;
  clearTimeout(state.entryAutosave.timer);
  state.entryAutosave.timer = null;
  state.entryAutosave.pending = null;
  await api(`/api/entries/${id}`, { method: "DELETE" });
  clearEntryForm();
  setStatus("Entry deleted");
  await loadEntries();
  await loadSummary();
}

async function loadPerson() {
  const person = await api("/api/person");
  const form = $("#personForm");
  Object.entries(person).forEach(([key, value]) => {
    const input = form.elements.namedItem(key);
    if (input) input.value = value || "";
  });
  renderPersonPortrait(person);
  await loadPersonIdentifiers();
}

function renderPersonPortrait(person = {}) {
  const image = $("#personPortraitImage");
  const placeholder = $("#personPortraitPlaceholder");
  const removeButton = $("#removePersonPortrait");
  const status = $("#personPortraitStatus");
  const available = Boolean(person.portrait_available);
  image.hidden = !available;
  placeholder.hidden = available;
  removeButton.hidden = !available;
  if (available) {
    const name = person.display_name || person.full_name || "researcher";
    image.alt = `Portrait of ${name}`;
    image.src = `/api/person/portrait?v=${Date.now()}`;
    status.textContent = person.portrait_filename
      ? `${person.portrait_filename} · stored in this VitaMine database.`
      : "Profile picture stored in this VitaMine database.";
  } else {
    image.removeAttribute("src");
    image.alt = "";
    status.textContent = "JPEG or PNG, up to 25 MB. Saved as PNG.";
  }
}

async function uploadPersonPortrait(event) {
  const input = event.currentTarget;
  const file = input.files?.[0];
  if (!file) return;
  const status = $("#personPortraitStatus");
  if (file.size > 25 * 1024 * 1024) {
    status.textContent = "The original portrait must be 25 MB or smaller.";
    input.value = "";
    return;
  }
  const form = new FormData();
  form.append("file", file, file.name);
  status.textContent = "Optimizing and saving picture…";
  input.disabled = true;
  try {
    const result = await api("/api/person/portrait", { method: "PUT", body: form });
    renderPersonPortrait(result);
    setStatus("Profile picture saved");
  } catch (error) {
    status.textContent = error.message;
    setStatus(error.message, { error: true });
  } finally {
    input.disabled = false;
    input.value = "";
  }
}

async function removePersonPortrait() {
  if (!window.confirm("Remove this profile picture from the VitaMine database and public profile?")) return;
  const button = $("#removePersonPortrait");
  const status = $("#personPortraitStatus");
  button.disabled = true;
  status.textContent = "Removing picture…";
  try {
    const result = await api("/api/person/portrait", { method: "DELETE" });
    renderPersonPortrait(result);
    setStatus("Profile picture removed");
  } catch (error) {
    status.textContent = error.message;
    setStatus(error.message, { error: true });
  } finally {
    button.disabled = false;
  }
}

async function loadPersonIdentifiers() {
  const data = await api("/api/person/identifiers");
  const rows = data.identifiers || [];
  state.identifiers = rows;
  $("#personIdentifiers").innerHTML = rows.length
    ? rows.map((row) => {
        const value = row.identifier_value ? `<strong>${escapeHtml(row.identifier_value)}</strong>` : "";
        const note = row.notes ? `<div class="identifierNote">${escapeHtml(row.notes)}</div>` : "";
        return `
          <article class="identifierItem">
            <div class="identifierContent">
              <div class="identifierTitle">${escapeHtml(row.platform || "")}</div>
              <div class="identifierMeta">${escapeHtml(row.identifier_type || "")} ${value}</div>
              ${note}
            </div>
            <div class="identifierActions">
              <button type="button" data-edit-identifier="${row.id}">Edit</button>
              <a href="${escapeHtml(row.url || "#")}" target="_blank" rel="noreferrer">Open</a>
            </div>
          </article>
        `;
      }).join("")
    : '<p class="emptyText">No identifiers stored yet.</p>';
  $$("[data-edit-identifier]").forEach((button) => {
    button.addEventListener("click", () => editIdentifier(Number(button.dataset.editIdentifier)));
  });
}

function clearIdentifierForm() {
  $("#identifierForm").reset();
  $("#identifierId").value = "";
  $("#identifierDialogTitle").textContent = "Add identifier";
  $("#deleteIdentifier").hidden = true;
  setIdentifierFormError("");
}

function populateIdentifierForm(id) {
  const row = state.identifiers.find((identifier) => identifier.id === id);
  if (!row) return false;
  $("#identifierId").value = row.id;
  $("#identifierPlatform").value = row.platform || "";
  $("#identifierType").value = row.identifier_type || "";
  $("#identifierValue").value = row.identifier_value || "";
  $("#identifierUrl").value = row.url || "";
  $("#identifierNotes").value = row.notes || "";
  $("#identifierDialogTitle").textContent = "Edit identifier";
  $("#deleteIdentifier").hidden = false;
  return true;
}

function setIdentifierFormError(message = "") {
  const error = $("#identifierFormError");
  error.textContent = message;
  error.hidden = !message;
}

function openIdentifierDialog(id = null) {
  clearIdentifierForm();
  if (id && !populateIdentifierForm(id)) return;
  const dialog = $("#identifierDialog");
  if (dialog.showModal) dialog.showModal();
  else dialog.setAttribute("open", "");
  $("#identifierPlatform").focus();
}

function closeIdentifierDialog() {
  const dialog = $("#identifierDialog");
  if (dialog.close) dialog.close();
  else dialog.removeAttribute("open");
  clearIdentifierForm();
}

function editIdentifier(id) {
  openIdentifierDialog(id);
}

function identifierPayload() {
  const id = Number($("#identifierId").value || 0);
  const current = state.identifiers.find((identifier) => identifier.id === id);
  return {
    platform: $("#identifierPlatform").value,
    identifier_type: $("#identifierType").value,
    identifier_value: $("#identifierValue").value,
    url: $("#identifierUrl").value,
    source: current?.source || "manual",
    notes: $("#identifierNotes").value,
  };
}

function autofillIdentifierUrl() {
  const platform = $("#identifierPlatform").value.trim().toLowerCase();
  const value = $("#identifierValue").value.trim();
  if (!$("#identifierUrl").value.trim() && platform === "orcid" && value) {
    $("#identifierUrl").value = `https://orcid.org/${value}`;
  }
}

async function saveIdentifier(event) {
  event.preventDefault();
  autofillIdentifierUrl();
  setIdentifierFormError("");
  const saveButton = $("#identifierForm button[type='submit']");
  saveButton.disabled = true;
  try {
    const id = $("#identifierId").value;
    const path = id ? `/api/person/identifiers/${id}` : "/api/person/identifiers";
    const method = id ? "PUT" : "POST";
    const payload = identifierPayload();
    await api(path, { method, body: JSON.stringify(payload) });
    setStatus("Identifier saved");
    closeIdentifierDialog();
    await loadPersonIdentifiers();
    await loadConnections();
    if (payload.platform.trim().toLowerCase() === "orcid") {
      pollAutomaticInstitutionMapping();
    }
  } catch (error) {
    setIdentifierFormError(error.message);
    setStatus(error.message);
  } finally {
    saveButton.disabled = false;
  }
}

async function deleteIdentifier() {
  const id = $("#identifierId").value;
  if (!id) return;
  setIdentifierFormError("");
  const button = $("#deleteIdentifier");
  button.disabled = true;
  try {
    await api(`/api/person/identifiers/${id}`, { method: "DELETE" });
    setStatus("Identifier deleted");
    closeIdentifierDialog();
    await loadPersonIdentifiers();
    await loadConnections();
  } catch (error) {
    setIdentifierFormError(error.message);
    setStatus(error.message);
  } finally {
    button.disabled = false;
  }
}

function personPayload() {
  const payload = {};
  new FormData($("#personForm")).forEach((value, key) => {
    payload[key] = value;
  });
  return payload;
}

function setPersonAutosaveStatus(text, stateName = "") {
  const status = $("#personAutosaveStatus");
  if (!status) return;
  status.textContent = text;
  status.dataset.state = stateName;
}

async function runPersonAutosave() {
  clearTimeout(state.personAutosave.timer);
  state.personAutosave.timer = null;
  if (state.personAutosave.saving || !state.personAutosave.pending) return;

  const snapshot = state.personAutosave.pending;
  state.personAutosave.pending = null;
  state.personAutosave.saving = true;
  let retryDelay = 100;
  setPersonAutosaveStatus("Saving changes…", "saving");
  try {
    const result = await api("/api/person", {
      method: "PUT",
      body: JSON.stringify(snapshot.payload),
    });
    setPersonAutosaveStatus(
      state.personAutosave.pending ? "Unsaved changes" : "All changes saved",
      state.personAutosave.pending ? "pending" : "saved",
    );
    $("#globalActivity")?.classList.remove("hasError");
    if (result.institution_mapping_pending) {
      pollAutomaticInstitutionMapping();
    } else if (snapshot.refreshMap) {
      try {
        await loadCollaborationMap();
      } catch (_error) {
        // The person record is already saved; the map can refresh later.
      }
    }
  } catch (error) {
    retryDelay = 1200;
    if (!state.personAutosave.pending) {
      state.personAutosave.pending = snapshot;
    } else {
      state.personAutosave.pending.refreshMap =
        state.personAutosave.pending.refreshMap || snapshot.refreshMap;
    }
    setPersonAutosaveStatus("Couldn’t save yet · retrying", "error");
    setStatus(error.message, { log: false, error: true });
  } finally {
    state.personAutosave.saving = false;
    if (state.personAutosave.pending) {
      state.personAutosave.timer = setTimeout(runPersonAutosave, retryDelay);
    }
  }
}

function schedulePersonAutosave(event, { immediate = false } = {}) {
  const institutionField = String(event?.target?.name || "").startsWith("own_institution_");
  state.personAutosave.pending = {
    payload: personPayload(),
    refreshMap: Boolean(state.personAutosave.pending?.refreshMap || institutionField),
  };
  clearTimeout(state.personAutosave.timer);
  if (immediate) {
    runPersonAutosave();
  } else {
    setPersonAutosaveStatus("Unsaved changes", "pending");
    state.personAutosave.timer = setTimeout(runPersonAutosave, 400);
  }
}

function submitPersonAutosave(event) {
  event.preventDefault();
  schedulePersonAutosave(event, { immediate: true });
}

function pollAutomaticInstitutionMapping(attempt = 0) {
  if (attempt === 0) {
    if (state.institutionMappingPollActive) return;
    state.institutionMappingPollActive = true;
  }
  const delays = [1200, 2200, 3500, 5000];
  if (attempt >= delays.length) {
    state.institutionMappingPollActive = false;
    return;
  }
  setTimeout(async () => {
    try {
      const status = await api("/api/person/institution-mapping-status");
      if (status.mapped && !status.pending) {
        await loadCollaborationMap();
        state.institutionMappingPollActive = false;
        return;
      }
      if (!status.pending) {
        state.institutionMappingPollActive = false;
        return;
      }
    } catch (_error) {
      // The mapping task is best-effort and a normal page refresh will retry.
    }
    pollAutomaticInstitutionMapping(attempt + 1);
  }, delays[attempt]);
}

async function loadNarrativeReport() {
  const report = await api("/api/narrative-report");
  $("#narrativeTitle").value = report.title || "Narrative Report";
  $("#narrativeBody").value = report.body || "";
  $("#narrativeTitleDe").value = report.title_de || "Freie Stellungname";
  $("#narrativeBodyDe").value = report.body_de || "";
}

async function saveNarrativeReport(event) {
  event.preventDefault();
  await api("/api/narrative-report", {
    method: "PUT",
    body: JSON.stringify({
      title: $("#narrativeTitle").value,
      body: $("#narrativeBody").value,
      title_de: $("#narrativeTitleDe").value,
      body_de: $("#narrativeBodyDe").value,
    }),
  });
  setStatus("Narrative report saved");
}

function r4riContributionMarkup(section) {
  const key = escapeHtml(section.section_key || "");
  const title = escapeHtml(section.title || "Contribution");
  return `
    <article class="r4riContribution" data-r4ri-section="${key}">
      <label>English heading<input data-r4ri-field="title" value="${title}"></label>
      <label>English contribution<textarea data-r4ri-field="body" rows="5">${escapeHtml(section.body || "")}</textarea></label>
      <label>German heading (optional)<input data-r4ri-field="title_de" value="${escapeHtml(section.title_de || "")}"></label>
      <label>German contribution (optional)<textarea data-r4ri-field="body_de" rows="5">${escapeHtml(section.body_de || "")}</textarea></label>
    </article>`;
}

async function loadR4riContributions() {
  const data = await api("/api/r4ri-contributions");
  const container = $("#r4riContributionFields");
  if (container) container.innerHTML = (data.sections || []).map(r4riContributionMarkup).join("");
}

async function saveR4riContributions() {
  const sections = $$("[data-r4ri-section]").map((card) => {
    const field = (name) => card.querySelector(`[data-r4ri-field="${name}"]`)?.value || "";
    return {
      section_key: card.dataset.r4riSection,
      title: field("title"),
      body: field("body"),
      title_de: field("title_de"),
      body_de: field("body_de"),
    };
  });
  await api("/api/r4ri-contributions", { method: "PUT", body: JSON.stringify({ sections }) });
  setStatus("R4RI contributions saved");
}

function publicationCategoryLabel(key) {
  const option = PUBLICATION_CATEGORIES.find((item) => item.key === key);
  return option ? option.label : String(key || "other").replace(/_/g, " ");
}

function loadPublicationCategoryFilters() {
  try {
    const saved = JSON.parse(window.localStorage.getItem("vitamine.publicationCategories") || "[]");
    if (Array.isArray(saved) && saved.length) {
      state.publicationCategoryFilters = new Set(saved);
      return;
    }
  } catch {
    // Keep defaults when local storage contains older or invalid values.
  }
  state.publicationCategoryFilters = new Set(DEFAULT_PUBLICATION_CATEGORIES);
}

function renderPublicationCategoryFilters() {
  const container = $("#publicationCategoryFilters");
  if (!container) return;
  container.innerHTML = PUBLICATION_CATEGORIES.map((option) => `<label class="inlineCheck">
    <input class="publicationCategoryFilter" type="checkbox" value="${escapeHtml(option.key)}" ${state.publicationCategoryFilters.has(option.key) ? "checked" : ""}>
    ${escapeHtml(option.label)}
  </label>`).join("");
  $$(".publicationCategoryFilter").forEach((input) => {
    input.addEventListener("change", () => {
      const selected = $$(".publicationCategoryFilter:checked").map((item) => item.value);
      state.publicationCategoryFilters = new Set(selected);
      window.localStorage.setItem("vitamine.publicationCategories", JSON.stringify(selected));
      loadPublications();
    });
  });
}

function renderPublicationCategoryOptions() {
  const select = $("#publicationCategory");
  if (!select) return;
  select.innerHTML = PUBLICATION_CATEGORIES.map((option) => `<option value="${escapeHtml(option.key)}">${escapeHtml(option.label)}</option>`).join("");
}

function setPublicationCategoryValue(value) {
  const select = $("#publicationCategory");
  if (!select) return;
  const category = value || "peer_reviewed";
  if (![...select.options].some((option) => option.value === category)) {
    select.insertAdjacentHTML("beforeend", `<option value="${escapeHtml(category)}">${escapeHtml(publicationCategoryLabel(category))}</option>`);
  }
  select.value = category;
}

function filteredPublications(publications) {
  return publications.filter((pub) => state.publicationCategoryFilters.has(pub.category || "other"));
}

function groupedPublications(publications) {
  const groups = new Map();
  publications.forEach((pub) => {
    const key = pub.category || "other";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(pub);
  });
  const orderedKeys = PUBLICATION_CATEGORIES.map((option) => option.key)
    .filter((key) => groups.has(key))
    .concat(Array.from(groups.keys()).filter((key) => !PUBLICATION_CATEGORIES.some((option) => option.key === key)).sort());
  return orderedKeys.map((key) => ({ key, publications: groups.get(key) || [] }));
}

function publicationRow(pub) {
  return `<tr class="${pub.suppress_display ? "mutedRow" : ""}" draggable="true" data-publication-id="${pub.id}">
    <td>${escapeHtml(pub.year || "")}</td>
    <td>${publicationSource(pub)}</td>
    <td>${publicationFlags(pub)}</td>
    <td>${escapeHtml(pub.selected_order || "")}</td>
    <td>${escapeHtml(pub.title || "")}${pub.quality_note ? `<div class="qualityNote">${escapeHtml(pub.quality_note)}</div>` : ""}</td>
    <td>${escapeHtml(pub.venue || "")}</td>
    <td>${impactFactor(pub)}</td>
    <td>${escapeHtml(pub.doi || "")}</td>
  </tr>`;
}

async function loadPublications() {
  const requestSequence = ++state.publicationLoadSequence;
  const params = new URLSearchParams();
  const q = $("#pubSearch").value.trim();
  if (q) params.set("q", q);
  if ($("#showSuppressedPubs").checked) params.set("show_suppressed", "1");
  params.set("sort", state.publicationSort.key);
  params.set("direction", state.publicationSort.direction);
  const data = await api(`/api/publications?${params.toString()}`);
  // Typing can start while the initial, unfiltered table is still loading.
  // Do not let an older response repaint over newer search results.
  if (requestSequence !== state.publicationLoadSequence) return;
  state.publications = data.publications;
  const filtered = filteredPublications(data.publications);
  $("#publicationsBody").innerHTML = groupedPublications(filtered)
    .map((group) => `<tr class="publicationCategoryRow">
        <th colspan="8">${escapeHtml(publicationCategoryLabel(group.key))}<span>${group.publications.length}</span></th>
      </tr>
      ${group.publications.map(publicationRow).join("")}`)
    .join("") || `<tr><td colspan="8" class="emptyCell">No matching publications.</td></tr>`;
  $$("#publicationsBody tr[data-publication-id]").forEach((row) => {
    row.classList.toggle("selected", Number(row.dataset.publicationId) === state.selectedPublicationId);
    row.addEventListener("click", () => {
      if (state.suppressPublicationClick) return;
      editPublication(Number(row.dataset.publicationId));
    });
    row.addEventListener("dragstart", (event) => {
      state.suppressPublicationClick = true;
      setTimeout(() => {
        state.suppressPublicationClick = false;
      }, 150);
      state.draggedPublicationId = Number(row.dataset.publicationId);
      state.draggedDropProfile = null;
      state.draggedDropId = null;
      clearBiosketchDragState();
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("text/plain", String(state.draggedPublicationId));
    });
  });
  if (state.selectedPublicationId && !state.publications.some((pub) => pub.id === state.selectedPublicationId)) {
    clearPublicationForm();
  }
  renderPublicationSortIndicators();
}

function clearPublicationForm() {
  state.selectedPublicationId = null;
  $("#publicationForm").reset();
  $("#publicationId").value = "";
  $("#publicationVenuePropagation").hidden = true;
  $("#publicationApplyVenueToMatches").checked = false;
  $("#publicationApplyVenueToMatches").disabled = true;
  setPublicationCategoryValue("peer_reviewed");
  $$("#publicationsBody tr[data-publication-id]").forEach((row) => row.classList.remove("selected"));
}

function openPublicationEditor() {
  const dialog = $("#publicationDialog");
  if (dialog.showModal) dialog.showModal();
  else dialog.setAttribute("open", "");
}

function closePublicationEditor() {
  const dialog = $("#publicationDialog");
  if (dialog.close) dialog.close();
  else dialog.removeAttribute("open");
}

function openPublicationIdentifierImport() {
  const dialog = $("#publicationIdentifierDialog");
  if (dialog.showModal) dialog.showModal();
  else dialog.setAttribute("open", "");
  $("#publicationIdentifiers").focus();
}

function closePublicationIdentifierImport() {
  const dialog = $("#publicationIdentifierDialog");
  if (dialog.close) dialog.close();
  else dialog.removeAttribute("open");
}

function identifierImportSummary(data) {
  const lines = [
    `Requested: ${data.requested || 0}`,
    `Imported: ${data.imported || 0}`,
    `Already present: ${data.skipped || 0}`,
    `Unresolved: ${data.unresolved || 0}`,
    "",
  ];
  (data.results || []).forEach((row) => {
    const label = row.kind ? row.kind.toUpperCase() : "ID";
    const title = row.title ? ` - ${row.title}` : "";
    lines.push(`${row.status}: ${label} ${row.identifier}${title}`);
  });
  return lines.join("\n").trim();
}

async function importPublicationIdentifiers(event) {
  event.preventDefault();
  const text = $("#publicationIdentifiers").value.trim();
  if (!text) {
    setStatus("Paste at least one DOI or PubMed ID");
    return;
  }
  setActionButtons(true);
  const stopProcessing = startProcessing("Resolving publication identifiers…");
  try {
    const data = await api("/api/publications/import-identifiers", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    appendConsole(identifierImportSummary(data));
    setStatus(`Imported ${data.imported || 0} publication${data.imported === 1 ? "" : "s"} from identifiers`);
    await loadPublications();
    await loadSummary();
    await loadExportProfile("ultrashort");
    await loadExportProfile("short");
    await loadBiosketch();
  } catch (error) {
    setStatus(error.message, { error: true });
  } finally {
    stopProcessing();
    setActionButtons(false);
  }
}

function editPublication(id) {
  const pub = state.publications.find((row) => row.id === id);
  if (!pub) return;
  state.selectedPublicationId = id;
  $("#publicationId").value = pub.id;
  $("#publicationTitle").value = pub.title || "";
  $("#publicationAuthors").value = pub.authors || "";
  $("#publicationYear").value = pub.year || "";
  setPublicationCategoryValue(pub.category || "peer_reviewed");
  $("#publicationVenue").value = pub.venue || "";
  $("#publicationDoi").value = pub.doi || "";
  $("#publicationPmid").value = pub.pmid || "";
  $("#publicationUrl").value = pub.url || "";
  $("#publicationRawCitation").value = pub.raw_citation || "";
  $("#publicationShortCitation").value = pub.short_citation || "";
  $("#publicationIncludeUltra").checked = Boolean(pub.include_ultrashort);
  $("#publicationIncludeShort").checked = Boolean(pub.include_short);
  $("#publicationSuppress").checked = Boolean(pub.suppress_display);
  $("#publicationQualityNote").value = pub.quality_note || "";
  $("#publicationApplyVenueToMatches").checked = false;
  $("#publicationVenuePropagation").hidden = false;
  $$("#publicationsBody tr[data-publication-id]").forEach((row) => {
    row.classList.toggle("selected", Number(row.dataset.publicationId) === id);
  });
  openPublicationEditor();
  refreshPublicationVenuePropagation();
}

async function refreshPublicationVenuePropagation() {
  const publicationId = Number($("#publicationId").value || 0);
  const canonicalTitle = $("#publicationVenue").value.trim();
  const panel = $("#publicationVenuePropagation");
  const checkbox = $("#publicationApplyVenueToMatches");
  const count = $("#publicationVenueMatchCount");
  const hint = $("#publicationVenuePropagationHint");
  if (!publicationId || !canonicalTitle) {
    panel.hidden = true;
    checkbox.checked = false;
    checkbox.disabled = true;
    return;
  }
  panel.hidden = false;
  checkbox.checked = false;
  checkbox.disabled = true;
  count.textContent = "matching publications";
  hint.textContent = "Checking the local journal catalog…";
  const requestId = (state.publicationVenuePreviewRequest || 0) + 1;
  state.publicationVenuePreviewRequest = requestId;
  try {
    const data = await api("/api/journal-catalog/preview", {
      method: "POST",
      body: JSON.stringify({ publication_id: publicationId, canonical_title: canonicalTitle }),
    });
    if (state.publicationVenuePreviewRequest !== requestId) return;
    const matches = Number(data.matches || 0);
    count.textContent = `${matches} matching publication${matches === 1 ? "" : "s"}`;
    checkbox.disabled = matches < 2;
    hint.textContent = matches < 2
      ? "No other known aliases will be changed."
      : "This updates only titles already linked through this CV’s local journal catalog.";
  } catch (_error) {
    if (state.publicationVenuePreviewRequest !== requestId) return;
    hint.textContent = "The matching preview is unavailable; this publication can still be saved normally.";
  }
}

function publicationPayload() {
  return {
    title: $("#publicationTitle").value,
    authors: $("#publicationAuthors").value,
    year: $("#publicationYear").value,
    category: $("#publicationCategory").value,
    venue: $("#publicationVenue").value,
    doi: $("#publicationDoi").value,
    pmid: $("#publicationPmid").value,
    url: $("#publicationUrl").value,
    raw_citation: $("#publicationRawCitation").value,
    short_citation: $("#publicationShortCitation").value,
    include_ultrashort: $("#publicationIncludeUltra").checked,
    include_short: $("#publicationIncludeShort").checked,
    suppress_display: $("#publicationSuppress").checked,
    quality_note: $("#publicationQualityNote").value,
    apply_venue_to_matches: $("#publicationApplyVenueToMatches").checked,
  };
}

async function syncPublicationProfileFlag(profile, publicationId, include) {
  const current = state.exportProfiles[profile] || {};
  let ids = (current.selected || []).map((pub) => pub.id).filter((id) => id !== publicationId);
  if (include) ids = ids.concat(publicationId);
  await api(`/api/export-profiles/${profile}/publications`, {
    method: "PUT",
    body: JSON.stringify({
      publication_limit: Math.max(Number(current.settings?.publication_limit || 10), ids.length, 1),
      authorship_filter: current.settings?.authorship_filter || "first_last",
      publications: ids.map((id, index) => ({ id, order: index + 1 })),
    }),
  });
}

async function savePublication(event) {
  event.preventDefault();
  const id = $("#publicationId").value;
  const payload = publicationPayload();
  const path = id ? `/api/publications/${id}` : "/api/publications";
  const method = id ? "PUT" : "POST";
  closePublicationEditor();
  let data;
  try {
    data = await api(path, { method, body: JSON.stringify(payload) });
  } catch (error) {
    openPublicationEditor();
    setStatus(`Could not save publication: ${error.message}`);
    return;
  }
  state.selectedPublicationId = Number(id || data.id || 0) || null;
  if (state.selectedPublicationId) {
    await syncPublicationProfileFlag("ultrashort", state.selectedPublicationId, payload.include_ultrashort);
    await syncPublicationProfileFlag("short", state.selectedPublicationId, payload.include_short);
  }
  const journalUpdated = Number(data.journal_update?.updated || 0);
  setStatus(journalUpdated
    ? `Publication saved; canonical venue applied to ${journalUpdated} publication${journalUpdated === 1 ? "" : "s"}`
    : "Publication saved");
  await loadPublications();
  await loadSummary();
  await loadExportProfile("ultrashort");
  await loadExportProfile("short");
  await loadBiosketch();
}

async function deletePublication() {
  const id = $("#publicationId").value;
  if (!id) return;
  closePublicationEditor();
  try {
    await api(`/api/publications/${id}`, { method: "DELETE" });
  } catch (error) {
    openPublicationEditor();
    setStatus(`Could not delete publication: ${error.message}`);
    return;
  }
  clearPublicationForm();
  setStatus("Publication deleted");
  await loadPublications();
  await loadSummary();
  await loadExportProfile("ultrashort");
  await loadExportProfile("short");
  await loadBiosketch();
}

async function loadJournalMetrics() {
  const params = new URLSearchParams();
  const q = $("#journalMetricSearch").value.trim();
  if (q) params.set("q", q);
  params.set("limit", "80");
  const data = await api(`/api/journal-metrics?${params.toString()}`);
  state.journalMetrics = data.metrics || [];
  renderJournalMetricEditor();
}

function renderJournalMetricEditor() {
  const rows = state.journalMetrics.map((row, index) => {
    const impact = row.impact_factor == null ? "" : row.impact_factor;
    return `<div class="metricRow" data-index="${index}">
      <div class="metricVenue"><strong>${escapeHtml(row.venue)}</strong><span>${row.count} publications</span></div>
      <input class="metricIf" inputmode="decimal" placeholder="IF" value="${escapeHtml(impact)}">
      <input class="metricYear" inputmode="numeric" placeholder="Year" value="${escapeHtml(row.impact_factor_year || "")}">
      <input class="metricSource" placeholder="Source" value="${escapeHtml(row.metric_source || "")}">
    </div>`;
  });
  $("#journalMetricEditor").innerHTML = rows.join("") || `<p class="emptyState">No venues found.</p>`;
}

function journalMetricPayload() {
  return {
    metrics: $$("#journalMetricEditor .metricRow").map((row) => {
      const source = state.journalMetrics[Number(row.dataset.index)];
      return {
        venue: source.venue,
        impact_factor: row.querySelector(".metricIf").value,
        impact_factor_year: row.querySelector(".metricYear").value,
        metric_source: row.querySelector(".metricSource").value,
      };
    }),
  };
}

async function saveJournalMetrics() {
  setStatus("Saving journal metrics...");
  setActionButtons(true);
  try {
    const data = await api("/api/journal-metrics", { method: "PUT", body: JSON.stringify(journalMetricPayload()) });
    actionLog("#syncOutput", data);
    setStatus("Journal metrics saved");
    await loadMetrics();
    await loadJournalMetrics();
    await loadPublications();
  } finally {
    setActionButtons(false);
  }
}

const exportUi = {
  short: {},
  ultrashort: {},
};

async function loadExportProfile(profile) {
  const ui = exportUi[profile];
  const params = new URLSearchParams();
  const search = ui.search ? $(ui.search) : null;
  const q = search?.value.trim();
  if (q) params.set("q", q);
  params.set("limit", "200");
  const data = await api(`/api/export-profiles/${profile}/publications?${params.toString()}`);
  state.exportProfiles[profile] = data;
  if (ui.limit && $(ui.limit)) $(ui.limit).value = data.settings?.publication_limit || 10;
  if (ui.authorship && $(ui.authorship)) $(ui.authorship).value = data.settings?.authorship_filter || "first_last";
  if (ui.selection && $(ui.selection)) renderExportSelection(profile);
  renderPublicationDropList(profile);
}

function selectedExportIds(profile) {
  const ui = exportUi[profile];
  return $$(`${ui.selection} .publicationPick:checked`).map((input) => Number(input.value));
}

function exportPublicationRow(profile, pub, selectedIds, index) {
  const checked = selectedIds.includes(pub.id) ? "checked" : "";
  const score = pub.score == null ? "" : `Score ${pub.score}`;
  const impact = pub.impact_factor == null ? "" : `IF ${pub.impact_factor}`;
  const meta = [pub.year, pub.venue, impact, pub.authorship, score].filter(Boolean).join(" · ");
  return `<label class="publicationPickRow">
    <input class="publicationPick" type="checkbox" value="${pub.id}" ${checked}>
    <input class="publicationOrder" type="number" min="1" value="${index + 1}" aria-label="Order">
    <span>
      <strong>${escapeHtml(pub.title || "")}</strong>
      <small>${escapeHtml(meta)}</small>
    </span>
  </label>`;
}

function renderExportSelection(profile) {
  const ui = exportUi[profile];
  const data = state.exportProfiles[profile];
  const selected = data.selected || [];
  const selectedIds = selected.map((pub) => pub.id);
  const candidateRows = (data.candidates || []).filter((pub) => !selectedIds.includes(pub.id));
  const rows = selected.concat(candidateRows);
  const selection = $(ui.selection);
  if (!selection) return;
  selection.innerHTML = rows.map((pub, index) => exportPublicationRow(profile, pub, selectedIds, index)).join("")
    || `<p class="emptyState">No matching publications found.</p>`;
}

function exportSelectionPayload(profile) {
  const ui = exportUi[profile];
  const rows = $$(`${ui.selection} .publicationPickRow`)
    .map((row) => ({
      id: Number(row.querySelector(".publicationPick").value),
      selected: row.querySelector(".publicationPick").checked,
      order: Number(row.querySelector(".publicationOrder").value || 999),
    }))
    .filter((row) => row.selected)
    .sort((a, b) => a.order - b.order);
  return {
    publication_limit: Number($(ui.limit)?.value || 10),
    authorship_filter: $(ui.authorship)?.value || "first_last",
    publications: rows,
  };
}

async function saveExportSelection(profile) {
  await api(`/api/export-profiles/${profile}/publications`, {
    method: "PUT",
    body: JSON.stringify(exportSelectionPayload(profile)),
  });
  setStatus(`${profile === "short" ? "Short CV" : "One-page CV"} selection saved`);
  await loadExportProfile(profile);
  await loadMetrics();
  await loadPublications();
}

function publicationSummary(pub) {
  const impact = pub.impact_factor == null ? "" : `IF ${pub.impact_factor}`;
  return [pub.year, pub.venue, impact].filter(Boolean).join(" · ");
}

function renderPublicationDropList(profile) {
  const data = state.exportProfiles[profile] || {};
  const selected = data.selected || [];
  const list = $(`#${profile}DropList`);
  const count = $(`#${profile}DropCount`);
  if (!list || !count) return;
  count.textContent = selected.length;
  list.innerHTML = selected.length
    ? selected.map((pub, index) => `
      <article class="dropItem" draggable="true" data-drop-id="${pub.id}" data-profile="${profile}">
        <span class="dropOrder">${index + 1}</span>
        <div>
          <strong>${escapeHtml(pub.title || "")}</strong>
          <small>${escapeHtml(publicationSummary(pub))}</small>
        </div>
        <button type="button" data-remove-drop="${pub.id}" data-profile="${profile}">Remove</button>
      </article>
    `).join("")
    : `<p class="emptyState">Drop publications here.</p>`;
  wireDropList(profile);
}

function wireDropList(profile) {
  const list = $(`#${profile}DropList`);
  if (!list) return;
  list.ondragover = (event) => {
    event.preventDefault();
    list.classList.add("dragOver");
  };
  list.ondragleave = () => list.classList.remove("dragOver");
  list.ondrop = async (event) => {
    event.preventDefault();
    list.classList.remove("dragOver");
    const targetItem = event.target.closest(".dropItem");
    const beforeId = targetItem ? Number(targetItem.dataset.dropId) : null;
    if (state.draggedDropProfile === profile && state.draggedDropId) {
      await reorderDropPublication(profile, state.draggedDropId, beforeId);
    } else if (state.draggedPublicationId) {
      await addDropPublication(profile, state.draggedPublicationId, beforeId);
    }
  };
  list.querySelectorAll(".dropItem").forEach((item) => {
    item.addEventListener("dragstart", (event) => {
      state.draggedDropProfile = profile;
      state.draggedDropId = Number(item.dataset.dropId);
      state.draggedPublicationId = null;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(state.draggedDropId));
    });
  });
  list.querySelectorAll("[data-remove-drop]").forEach((button) => {
    button.addEventListener("click", () => removeDropPublication(button.dataset.profile, Number(button.dataset.removeDrop)));
  });
}

function selectedDropIds(profile) {
  return (state.exportProfiles[profile]?.selected || []).map((pub) => pub.id);
}

async function saveDropIds(profile, ids) {
  await api(`/api/export-profiles/${profile}/publications`, {
    method: "PUT",
    body: JSON.stringify({
      publication_limit: ids.length || 1,
      authorship_filter: state.exportProfiles[profile]?.settings?.authorship_filter || "first_last",
      publications: ids.map((id, index) => ({ id, order: index + 1 })),
    }),
  });
  setStatus(`${profile === "short" ? "Short CV" : "One-page CV"} selection saved`);
  await loadExportProfile(profile);
  await loadMetrics();
  await loadPublications();
}

async function addDropPublication(profile, publicationId, beforeId = null) {
  if (beforeId === publicationId) return;
  const ids = selectedDropIds(profile).filter((id) => id !== publicationId);
  const insertAt = beforeId ? ids.indexOf(beforeId) : -1;
  if (insertAt >= 0) ids.splice(insertAt, 0, publicationId);
  else ids.push(publicationId);
  await saveDropIds(profile, ids);
}

async function reorderDropPublication(profile, publicationId, beforeId = null) {
  await addDropPublication(profile, publicationId, beforeId);
}

async function removeDropPublication(profile, publicationId) {
  await saveDropIds(profile, selectedDropIds(profile).filter((id) => id !== publicationId));
}

async function loadBiosketch() {
  const data = await api("/api/biosketch");
  state.biosketch = data;
  renderBiosketchEditor();
}

function renderBiosketchEditor() {
  const container = $("#biosketchContributions");
  const count = $("#biosketchDropCount");
  if (!container || !count) return;
  const contributions = state.biosketch.contributions || [];
  const total = state.biosketch.publication_count || 0;
  const limit = state.biosketch.publication_limit || 20;
  const contributionLimit = state.biosketch.contribution_limit || 5;
  const productsPerContributionLimit = state.biosketch.products_per_contribution_limit || 4;
  const contributionOverLimit = contributions.length > contributionLimit;
  const productOverLimit = contributions.some((contribution) => (contribution.publications || []).length > productsPerContributionLimit);
  count.textContent = `${total} / ${limit}`;
  count.title = `NIH legacy biosketch: up to ${contributionLimit} Contributions to Science, with up to ${productsPerContributionLimit} cited products each.`;
  count.classList.toggle("dangerCount", total > limit || contributionOverLimit || productOverLimit);
  container.innerHTML = contributions.length
    ? contributions.map((contribution) => biosketchContributionMarkup(contribution)).join("")
    : `<p class="emptyState">No Contributions to Science yet.</p>`;
  wireBiosketchEditor();
}

function biosketchContributionMarkup(contribution) {
  const pubs = contribution.publications || [];
  const selected = state.selectedBiosketchContributionId === contribution.id ? " selected" : "";
  const perContributionLimit = state.biosketch.products_per_contribution_limit || 4;
  const overLimit = pubs.length > perContributionLimit ? " overLimit" : "";
  return `
    <article class="biosketchContribution${selected}${overLimit}" data-contribution-id="${contribution.id}">
      <div class="biosketchContributionHeader">
        <span>${escapeHtml(String(contribution.ordinal || ""))}</span>
        <input class="biosketchTitle" value="${escapeHtml(contribution.title || "")}" aria-label="Contributions to Science title">
      </div>
      <textarea class="biosketchNarrative" rows="5" aria-label="Contributions to Science text">${escapeHtml(contribution.narrative || "")}</textarea>
      <div class="dropList biosketchDropList" data-contribution-id="${contribution.id}">
        ${pubs.length ? pubs.map((pub, index) => biosketchPublicationMarkup(pub, index, contribution.id)).join("") : `<p class="emptyState">Drop publications here.</p>`}
      </div>
    </article>
  `;
}

function biosketchPublicationMarkup(pub, index, contributionId) {
  return `
    <article class="dropItem biosketchPubItem" draggable="true" data-publication-id="${pub.id}" data-contribution-id="${contributionId}">
      <span class="dropOrder">${index + 1}</span>
      <div>
        <strong>${escapeHtml(pub.title || "")}</strong>
        <small>${escapeHtml(publicationSummary(pub))}</small>
      </div>
      <button type="button" data-remove-biosketch-pub="${pub.id}" data-contribution-id="${contributionId}">Remove</button>
    </article>
  `;
}

function wireBiosketchEditor() {
  $$(".biosketchContribution").forEach((contributionCard) => {
    const contributionId = Number(contributionCard.dataset.contributionId);
    const title = contributionCard.querySelector(".biosketchTitle");
    const narrative = contributionCard.querySelector(".biosketchNarrative");
    contributionCard.addEventListener("click", () => {
      if (state.selectedBiosketchContributionId === contributionId) return;
      state.selectedBiosketchContributionId = contributionId;
      $$(".biosketchContribution").forEach((item) => item.classList.remove("selected"));
      contributionCard.classList.add("selected");
    });
    const saveText = debounce(() => saveBiosketchContributionText(contributionId, title.value, narrative.value), 650);
    title.addEventListener("input", saveText);
    narrative.addEventListener("input", saveText);
  });
  $$(".biosketchDropList").forEach((list) => {
    const contributionId = Number(list.dataset.contributionId);
    list.ondragover = (event) => {
      event.preventDefault();
      list.classList.add("dragOver");
    };
    list.ondragleave = () => list.classList.remove("dragOver");
    list.ondrop = async (event) => {
      event.preventDefault();
      list.classList.remove("dragOver");
      const targetItem = event.target.closest(".biosketchPubItem");
      const beforeId = targetItem ? Number(targetItem.dataset.publicationId) : null;
      if (state.draggedBiosketchPublicationId) {
        await moveBiosketchPublication(
          state.draggedBiosketchContributionId,
          contributionId,
          state.draggedBiosketchPublicationId,
          beforeId,
        );
      } else if (state.draggedPublicationId) {
        await addBiosketchPublication(contributionId, state.draggedPublicationId, beforeId);
      }
      clearBiosketchDragState();
    };
  });
  $$(".biosketchPubItem").forEach((item) => {
    item.addEventListener("dragstart", (event) => {
      state.draggedBiosketchContributionId = Number(item.dataset.contributionId);
      state.draggedBiosketchPublicationId = Number(item.dataset.publicationId);
      state.draggedPublicationId = null;
      state.draggedDropProfile = null;
      state.draggedDropId = null;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(state.draggedBiosketchPublicationId));
    });
  });
  $$("[data-remove-biosketch-pub]").forEach((button) => {
    button.addEventListener("click", () => {
      removeBiosketchPublication(Number(button.dataset.contributionId), Number(button.dataset.removeBiosketchPub));
    });
  });
}

function clearBiosketchDragState() {
  state.draggedBiosketchContributionId = null;
  state.draggedBiosketchPublicationId = null;
}

function biosketchContribution(contributionId) {
  return (state.biosketch.contributions || []).find((item) => item.id === contributionId);
}

function biosketchPublicationIds(contributionId) {
  return (biosketchContribution(contributionId)?.publications || []).map((pub) => pub.id).filter(Boolean);
}

async function saveBiosketchContributionText(contributionId, title, narrative) {
  if (!title.trim()) return;
  await api(`/api/biosketch/contributions/${contributionId}`, {
    method: "PUT",
    body: JSON.stringify({ title, narrative }),
  });
  setStatus("Biosketch Contributions to Science item saved");
}

async function saveBiosketchPublicationIds(contributionId, ids) {
  await api(`/api/biosketch/contributions/${contributionId}/publications`, {
    method: "PUT",
    body: JSON.stringify({ publications: ids.map((id) => ({ id })) }),
  });
}

async function addBiosketchPublication(contributionId, publicationId, beforeId = null) {
  if (!publicationId) return;
  const ids = biosketchPublicationIds(contributionId).filter((id) => id !== publicationId);
  const insertAt = beforeId ? ids.indexOf(beforeId) : -1;
  if (insertAt >= 0) ids.splice(insertAt, 0, publicationId);
  else ids.push(publicationId);
  await saveBiosketchPublicationIds(contributionId, ids);
  setStatus("Biosketch publications saved");
  await loadBiosketch();
}

async function moveBiosketchPublication(fromContributionId, toContributionId, publicationId, beforeId = null) {
  if (!publicationId || !toContributionId) return;
  if (fromContributionId && fromContributionId !== toContributionId) {
    await saveBiosketchPublicationIds(
      fromContributionId,
      biosketchPublicationIds(fromContributionId).filter((id) => id !== publicationId),
    );
  }
  await addBiosketchPublication(toContributionId, publicationId, beforeId);
}

async function removeBiosketchPublication(contributionId, publicationId) {
  await saveBiosketchPublicationIds(contributionId, biosketchPublicationIds(contributionId).filter((id) => id !== publicationId));
  setStatus("Biosketch publication removed");
  await loadBiosketch();
}

async function createBiosketchContribution() {
  const data = await api("/api/biosketch/contributions", {
    method: "POST",
    body: JSON.stringify({ title: "New Contributions to Science Item", narrative: "" }),
  });
  state.selectedBiosketchContributionId = data.id;
  setStatus("Biosketch Contributions to Science item created");
  await loadBiosketch();
}

async function deleteBiosketchContribution() {
  const contributions = state.biosketch.contributions || [];
  const contribution =
    contributions.find((item) => item.id === state.selectedBiosketchContributionId) || contributions[contributions.length - 1];
  if (!contribution) return;
  await api(`/api/biosketch/contributions/${contribution.id}`, { method: "DELETE" });
  state.selectedBiosketchContributionId = null;
  setStatus("Biosketch Contributions to Science item deleted");
  await loadBiosketch();
}

async function suggestExportSelection(profile) {
  const ui = exportUi[profile];
  await api(`/api/export-profiles/${profile}/suggest`, {
    method: "POST",
    body: JSON.stringify({
      publication_limit: Number($(ui.limit)?.value || 10),
      authorship_filter: $(ui.authorship)?.value || "first_last",
    }),
  });
  setStatus(`${profile === "short" ? "Short CV" : "One-page CV"} suggestions applied`);
  await loadExportProfile(profile);
  await loadMetrics();
  await loadPublications();
}

function renderPublicationSortIndicators() {
  $$("#publicationsView th[data-sort]").forEach((header) => {
    const active = header.dataset.sort === state.publicationSort.key;
    header.classList.toggle("sorted", active);
    header.dataset.direction = active ? state.publicationSort.direction : "";
  });
}

function sortPublicationsBy(key) {
  if (state.publicationSort.key === key) {
    state.publicationSort.direction = state.publicationSort.direction === "asc" ? "desc" : "asc";
  } else {
    state.publicationSort = { key, direction: key === "title" || key === "venue" || key === "source" ? "asc" : "desc" };
  }
  loadPublications();
}

function publicationFlags(pub) {
  const flags = [];
  if (pub.include_ultrashort) flags.push("Ultra");
  if (pub.include_short) flags.push("Short");
  return flags.map((flag) => `<span class="flag">${flag}</span>`).join("");
}

function publicationSource(pub) {
  const parts = [];
  if (pub.source) parts.push(`<span class="sourceBadge">${escapeHtml(pub.source)}</span>`);
  if (pub.orcid_put_code) parts.push(`<span class="sourceBadge">ORCID</span>`);
  return parts.join("");
}

function impactFactor(pub) {
  if (pub.impact_factor == null) return "";
  const year = pub.impact_factor_year ? ` (${pub.impact_factor_year})` : "";
  return escapeHtml(`${pub.impact_factor}${year}`);
}

async function runAction(path, doneText, workingText = "Working...", options = {}) {
  const stopProcessing = startProcessing(workingText);
  setActionButtons(true);
  try {
    const submit = options.idempotent ? submitCloudJob : api;
    let data = await submit(path, { method: "POST" });
    if (data.background && data.job?.id) {
      state.cloud.activeJob = data.job;
      setCloudJobControls(true);
      data = await waitForCloudJob(data.job.id);
    }
    setStatus(doneText);
    await loadSummary();
    await loadMetrics();
    await loadCollaborationMap();
    await loadPublications();
    return data;
  } catch (error) {
    setStatus(error.message, { error: true });
    throw error;
  } finally {
    stopProcessing();
    setActionButtons(false);
  }
}

function switchView(name) {
  $$(".tab[data-view]").forEach((tab) => {
    const selected = tab.dataset.view === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", selected ? "true" : "false");
    tab.tabIndex = selected ? 0 : -1;
  });
  $$(".topbarViewButton[data-view]").forEach((button) => {
    const selected = button.dataset.view === name;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  $$(".view").forEach((view) => {
    const selected = view.id === `${name}View`;
    view.classList.toggle("active", selected);
    view.hidden = !selected;
  });
}

function enrichmentSummaryText(data) {
  const summary = data?.enrichment_summary;
  if (!summary) return "CV enrichment complete";
  const coverage = summary.citation_coverage || {};
  const staged = Number(summary.staged_from_sources || 0) + Number(summary.staged_from_web || 0);
  const reviewed = (
    Number(summary.matched_at_source || 0)
    + Number(summary.duplicates || 0)
    + Number(summary.backfilled || 0)
  );
  return [
    `${formatMetricNumber(summary.source_records_fetched || 0)} source records fetched`,
    `${formatMetricNumber(reviewed)} already present or backfilled`,
    `${formatMetricNumber(summary.rejected || 0)} rejected by safety checks`,
    `${formatMetricNumber(staged)} added to Inbox`,
    `OpenAlex citations for ${formatMetricNumber(coverage.publications_with_citations || 0)}/${formatMetricNumber(coverage.visible_publications || 0)} publications (${formatMetricNumber(coverage.citation_total || 0)} total)`,
  ].join(" · ");
}

function handleTabKeydown(event) {
  const tabs = $$(".tab[data-view]");
  const index = tabs.indexOf(event.currentTarget);
  if (index < 0) return;

  let nextIndex = null;
  if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
  if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
  if (event.key === "Home") nextIndex = 0;
  if (event.key === "End") nextIndex = tabs.length - 1;
  if (nextIndex === null) return;

  event.preventDefault();
  tabs[nextIndex].focus();
  switchView(tabs[nextIndex].dataset.view);
}

function debounce(fn, delay = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

async function loadStartupStep(label, loader) {
  try {
    await loader();
  } catch (error) {
    console.error(`${label} failed`, error);
    setStatus(`${label}: ${error.message || "Could not load"}`);
  }
}

async function init() {
  try {
    const response = await fetch("/gateway/workspace/status", { credentials: "same-origin" });
    if (response.ok) {
      const workspace = await response.json();
      configureCloudWorkspace(workspace);
      const databasePanel = $("#databasePanel");
      if (databasePanel) databasePanel.hidden = true;
      const databaseModeHelp = $("#databaseModeHelp");
      if (databaseModeHelp) databaseModeHelp.hidden = true;
      ["#renameDatabase", "#useExampleDatabase", "#createBlankDatabase", "#loadDatabase"].forEach((selector) => {
        const control = $(selector);
        if (control) control.hidden = true;
      });
    }
  } catch (error) {
    console.warn("Cloud workspace controls are unavailable:", error);
  }
  $("#brandHome").addEventListener("click", returnToWorkspaceHome);
  $$(".tab[data-view]").forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
    tab.addEventListener("keydown", handleTabKeydown);
  });
  $$(".topbarViewButton[data-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      switchView(button.dataset.view);
      if (button.dataset.view === "inbox" && state.onboarding?.step === "inbox") {
        await completeOnboarding();
      }
    });
  });
  loadPublicationCategoryFilters();
  renderPublicationCategoryFilters();
  renderPublicationCategoryOptions();
  switchView("dashboard");
  $("#sectionFilter").addEventListener("change", loadEntries);
  $("#entrySearch").addEventListener("input", debounce(loadEntries));
  $("#pubSearch").addEventListener("input", debounce(loadPublications));
  $("#showSuppressedPubs").addEventListener("change", loadPublications);
  $("#journalMetricSearch").addEventListener("input", debounce(loadJournalMetrics));
  $("#exportFormatSearch").addEventListener("input", renderExportFormats);
  $("#chooseCustomTemplateFile").addEventListener("click", () => $("#customTemplateFileInput").click());
  $("#customTemplateFileInput").addEventListener("change", (event) => importCustomExportTemplate(event.target.files?.[0]));
  $("#customTemplateDropzone").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      $("#customTemplateFileInput").click();
    }
  });
  $("#customTemplateDropzone").addEventListener("dragover", (event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    $("#customTemplateDropzone").classList.add("dragover");
  });
  $("#customTemplateDropzone").addEventListener("dragleave", () => $("#customTemplateDropzone").classList.remove("dragover"));
  $("#customTemplateDropzone").addEventListener("drop", (event) => {
    event.preventDefault();
    $("#customTemplateDropzone").classList.remove("dragover");
    importCustomExportTemplate(event.dataTransfer.files?.[0]);
  });
  $("#journalMetricEditor").addEventListener("change", debounce(saveJournalMetrics, 500));
  $("#connectionsForm").addEventListener("submit", saveConnections);
  $("#connectZotero").addEventListener("click", connectZotero);
  $("#disconnectZotero").addEventListener("click", disconnectZotero);
  $("#testZoteroConnection").addEventListener("click", testZoteroConnection);
  $("#loadZoteroCollections").addEventListener("click", loadZoteroCollections);
  $("#connectionZoteroSource").addEventListener("change", updateZoteroSourceVisibility);
  $("#inboxStatusFilter").addEventListener("change", () => loadImportInbox());
  $("#inboxTypeFilter").addEventListener("change", () => loadImportInbox());
  $("#selectInboxVisible").addEventListener("click", () => setVisibleInboxChecks(true));
  $("#acceptInboxSelected").addEventListener("click", () => acceptInboxItems("#inboxList"));
  $("#rejectInboxSelected").addEventListener("click", () => rejectInboxItems("#inboxList"));
  $("#restoreInboxSelected").addEventListener("click", () => restoreInboxItems("#inboxList"));
  $("#resolveInboxPublications").addEventListener("click", () => resolveInboxPublications("#inboxList"));
  $("#acceptHighConfidenceInbox").addEventListener("click", acceptHighConfidenceInbox);
  $("#rejectDuplicateInbox").addEventListener("click", rejectDuplicateInbox);
  $("#acceptReviewSelected").addEventListener("click", () => acceptInboxItems("#importReviewList"));
  $("#rejectReviewSelected").addEventListener("click", () => rejectInboxItems("#importReviewList"));
  $("#closeImportReview").addEventListener("click", () => $("#importReviewDialog").close());
  $("#profileSyncButton").addEventListener("click", openProfileSync);
  $("#closeProfileSync").addEventListener("click", () => $("#profileSyncDialog").close());
  $("#skipProfileSync").addEventListener("click", skipProfileSync);
  $("#applyProfileSync").addEventListener("click", applyProfileSync);
  $("#useExampleDatabase").addEventListener("click", useExampleDatabase);
  $("#renameDatabase").addEventListener("click", openRenameDatabaseDialog);
  $("#renameDatabaseForm").addEventListener("submit", renameDatabase);
  $("#closeRenameDatabaseDialog").addEventListener("click", closeRenameDatabaseDialog);
  $("#createBlankDatabase").addEventListener("click", openNewDatabaseDialog);
  $("#closeNewDatabaseDialog").addEventListener("click", closeNewDatabaseDialog);
  $("#newDatabaseDialog").addEventListener("click", (event) => {
    if (event.target === $("#newDatabaseDialog")) closeNewDatabaseDialog();
  });
  $("#newDatabaseName").addEventListener("input", () => setNewDatabaseError(""));
  $("#newDatabaseForm").addEventListener("submit", createBlankDatabase);
  $("#llmOnboardingForm").addEventListener("submit", saveOnboardingApiKey);
  $("#useLocalOnboardingModel").addEventListener("click", useLocalOnboardingModel);
  $("#onboardingApiKey").addEventListener("input", updateSimpleOnboardingActions);
  $("#showOnboardingAdvancedConfig").addEventListener("click", showAdvancedLlmDialog);
  $("#showOnboardingHowto").addEventListener("click", (event) => {
    const panel = $("#onboardingHowto");
    panel.hidden = !panel.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!panel.hidden));
  });
  $("#onboardingProvider").addEventListener("change", updateOnboardingProviderVisibility);
  $("#llmAdvancedForm").addEventListener("submit", saveOnboardingAdvancedConfig);
  $("#testOnboardingAdvancedConfig").addEventListener("click", testOnboardingAdvancedConfig);
  $("#closeLlmAdvancedDialog").addEventListener("click", closeAdvancedLlmDialog);
  $("#llmOnboardingDialog").addEventListener("close", () => {
    if (!state.pendingCvImportFiles.length && state.onboarding?.step === "import_cv") renderOnboardingCoach();
  });
  $("#llmOnboardingDialog").addEventListener("cancel", (event) => event.preventDefault());
  $("#llmAdvancedDialog").addEventListener("cancel", (event) => event.preventDefault());
  $("#skipOnboardingStep").addEventListener("click", skipOnboardingStep);
  $("#linkOrcid").addEventListener("click", openOrcidLinkDialog);
  $("#closeOrcidLinkDialog").addEventListener("click", () => $("#orcidLinkDialog").close());
  $("#connectOrcidOAuth").addEventListener("click", connectOrcidOAuth);
  $("#discoverOrcid").addEventListener("click", discoverOrcid);
  $("#orcidLinkForm").addEventListener("submit", linkOrcid);
  $("#loadDatabase").addEventListener("click", chooseDatabaseFile);
  on("#cvImportProvider", "change", updateCvImportProviderVisibility);
  on("#cvImportOpenAiModel", "change", updateCvImportProviderVisibility);
  on("#saveCvImportSettings", "click", saveCvImportSettings);
  on("#chooseCvImportFile", "click", () => $("#cvImportFileInput")?.click());
  on("#cvImportFileInput", "change", (event) => importCvFiles(event.target.files));
  on("#importCvDropzone", "dragover", (event) => {
    event.preventDefault();
    $("#importCvDropzone")?.classList.add("dragover");
  });
  on("#importCvDropzone", "dragleave", () => $("#importCvDropzone")?.classList.remove("dragover"));
  on("#importCvDropzone", "drop", (event) => {
    event.preventDefault();
    $("#importCvDropzone")?.classList.remove("dragover");
    importCvFiles(event.dataTransfer.files);
  });
  $("#databaseFileInput").addEventListener("change", async (event) => {
    try {
      await importDatabaseFile(event.target.files[0]);
    } catch (error) {
      setStatus(error.message);
    }
  });
  $("#newBiosketchContribution").addEventListener("click", createBiosketchContribution);
  $("#deleteBiosketchContribution").addEventListener("click", deleteBiosketchContribution);
  $$("#publicationsView th[data-sort]").forEach((header) => {
    header.addEventListener("click", () => sortPublicationsBy(header.dataset.sort));
  });
  $("#newPublication").addEventListener("click", () => {
    clearPublicationForm();
    openPublicationEditor();
  });
  $("#importPublicationIds").addEventListener("click", openPublicationIdentifierImport);
  $("#closePublicationIdentifierImport").addEventListener("click", closePublicationIdentifierImport);
  $("#publicationIdentifierDialog").addEventListener("click", (event) => {
    if (event.target === $("#publicationIdentifierDialog")) closePublicationIdentifierImport();
  });
  $("#publicationIdentifierForm").addEventListener("submit", importPublicationIdentifiers);
  $("#closePublicationEditor").addEventListener("click", closePublicationEditor);
  $("#publicationDialog").addEventListener("click", (event) => {
    if (event.target === $("#publicationDialog")) closePublicationEditor();
  });
  $("#publicationForm").addEventListener("submit", savePublication);
  $("#publicationVenue").addEventListener("change", refreshPublicationVenuePropagation);
  $("#deletePublication").addEventListener("click", deletePublication);
  $("#newEntry").addEventListener("click", clearEntryForm);
  $("#entryForm").addEventListener("submit", saveEntry);
  $("#translateEntryToAdditional").addEventListener("click", () => translateSelectedEntry("primary_to_additional"));
  $("#translateEntryToEnglish").addEventListener("click", () => translateSelectedEntry("additional_to_primary"));
  $("#entrySection").addEventListener("change", updateGrantStatusVisibility);
  $$("#entryForm input, #entryForm textarea, #entryForm select").forEach((field) => {
    if (field.type !== "hidden") field.addEventListener("input", scheduleEntryAutosave);
    if (field.type === "checkbox" || field.tagName === "SELECT") field.addEventListener("change", scheduleEntryAutosave);
  });
  $("#deleteEntry").addEventListener("click", deleteEntry);
  $("#personForm").addEventListener("submit", submitPersonAutosave);
  $("#personPortraitInput").addEventListener("change", uploadPersonPortrait);
  $("#removePersonPortrait").addEventListener("click", removePersonPortrait);
  $$("#personForm input[name], #personForm textarea[name], #personForm select[name]").forEach((field) => {
    field.addEventListener("input", schedulePersonAutosave);
    field.addEventListener("change", (event) => schedulePersonAutosave(event, { immediate: true }));
  });
  $("#identifierForm").addEventListener("submit", saveIdentifier);
  $("#identifierPlatform").addEventListener("input", autofillIdentifierUrl);
  $("#identifierValue").addEventListener("input", autofillIdentifierUrl);
  $("#addIdentifier").addEventListener("click", () => openIdentifierDialog());
  $("#closeIdentifierDialog").addEventListener("click", closeIdentifierDialog);
  $("#identifierDialog").addEventListener("click", (event) => {
    if (event.target === $("#identifierDialog")) closeIdentifierDialog();
  });
  $("#deleteIdentifier").addEventListener("click", deleteIdentifier);
  $("#narrativeForm").addEventListener("submit", saveNarrativeReport);
  $("#saveR4riContributions").addEventListener("click", saveR4riContributions);
  const enrichCv = async () => {
    if (state.onboarding?.step === "enrich") {
      const coach = $("#onboardingCoach");
      if (coach) coach.hidden = true;
      clearOnboardingTarget();
    }
    const enrichPath = state.cloud.enabled && state.cloud.workspace?.background_jobs
      ? "/api/cloud/jobs/enrich-cv"
      : "/api/actions/enrich-cv";
    const data = await runAction(
      enrichPath,
      "CV enrichment complete",
      "Enriching CV from databases and online sources...",
      { idempotent: enrichPath.startsWith("/api/cloud/jobs/") },
    );
    setStatus(enrichmentSummaryText(data));
    actionLog("#syncOutput", data);
    await loadImportInbox();
    await loadConnections();
    await loadPersonIdentifiers();
    await loadMetrics();
    await loadPublications();
    await loadOnboarding();
  };
  $("#enrichCvDashboard").addEventListener("click", () => {
    void (async () => {
      try {
        if (requirePlusUi() && await ensureOpenAiEnrichmentConsent()) await enrichCv();
      } catch (error) {
        setStatus(error.message, { error: true });
      }
    })();
  });
  const cleanupCv = async () => {
    const cleanupPath = state.cloud.enabled && state.cloud.workspace?.background_jobs
      ? "/api/cloud/jobs/cleanup-cv"
      : "/api/actions/cleanup-cv";
    const data = await runAction(
      cleanupPath,
      "CV cleanup suggestions are ready in the Inbox",
      "Reviewing compact CV sections for corrections, duplicates, and junk entries...",
      { idempotent: cleanupPath.startsWith("/api/cloud/jobs/") },
    );
    setStatus(`${data.suggestions_staged || 0} cleanup suggestions added to Inbox`);
    actionLog("#syncOutput", data);
    await loadImportInbox();
  };
  $("#cleanupCvDashboard").addEventListener("click", () => {
    if (requirePlusUi()) cleanupCv();
  });
  $("#workspacePlusButton")?.addEventListener("click", toggleWorkspaceDeveloperPlus);
  $("#closeWorkspacePlusDialog")?.addEventListener("click", () => $("#workspacePlusDialog")?.close());
  $("#exploreCitations")?.addEventListener("click", openCitationExplorer);
  $("#exploreCitationNetwork")?.addEventListener("click", openCitationNetwork);
  $("#closeCitationExplorer")?.addEventListener("click", () => $("#citationExplorerDialog")?.close());
  $("#citationExplorerDialog")?.addEventListener("click", (event) => {
    if (event.target === $("#citationExplorerDialog")) $("#citationExplorerDialog")?.close();
  });
  document.querySelectorAll("[data-citation-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      state.citationExplorer.sort = button.dataset.citationSort || "citations";
      renderCitationExplorer();
    });
  });
  $("#citationFirstLastOnly")?.addEventListener("change", (event) => {
    state.citationExplorer.firstLastOnly = event.currentTarget.checked;
    renderCitationExplorer();
  });
  $("#closeCitationCitedBy")?.addEventListener("click", () => $("#citationCitedByDialog")?.close());
  $("#citationCitedByDialog")?.addEventListener("click", (event) => {
    if (event.target === $("#citationCitedByDialog")) $("#citationCitedByDialog")?.close();
  });
  $("#loadMoreCitingWorks")?.addEventListener("click", () => {
    const publication = state.citationExplorer.selectedPublication;
    if (publication && state.citationExplorer.nextCitingPage) {
      openCitingWorks(publication.id, state.citationExplorer.nextCitingPage);
    }
  });
  $("#closeHIndexHistory")?.addEventListener("click", () => $("#hIndexHistoryDialog")?.close());
  $("#hIndexHistoryDialog")?.addEventListener("click", (event) => {
    if (event.target === $("#hIndexHistoryDialog")) $("#hIndexHistoryDialog")?.close();
  });
  $("#closeCitationNetwork")?.addEventListener("click", () => $("#citationNetworkDialog")?.close());
  $("#citationNetworkDialog")?.addEventListener("click", (event) => {
    if (event.target === $("#citationNetworkDialog")) $("#citationNetworkDialog")?.close();
  });
  $("#refreshCitationNetwork")?.addEventListener("click", () => {
    if (requirePlusUi()) queueCitationNetworkRefresh();
  });
  $("#citationNetworkZoomIn")?.addEventListener("click", () => state.citationNetwork.graph?.zoom({ level: state.citationNetwork.graph.zoom() * 1.25, renderedPosition: { x: 480, y: 300 } }));
  $("#citationNetworkZoomOut")?.addEventListener("click", () => state.citationNetwork.graph?.zoom({ level: state.citationNetwork.graph.zoom() / 1.25, renderedPosition: { x: 480, y: 300 } }));
  $("#citationNetworkFit")?.addEventListener("click", () => state.citationNetwork.graph?.fit(undefined, 72));
  $("#citationNetworkReset")?.addEventListener("click", () => runCitationNetworkLayout(state.citationNetwork.graph, { reset: true, fit: true }));
  document.querySelectorAll("[data-dashboard-map-mode]").forEach((button) => {
    button.addEventListener("click", () => selectDashboardMapMode(button.dataset.dashboardMapMode));
  });
  $("#homeLanguageLabel").addEventListener("change", saveExportSettings);
  $("#homeLanguageCode").addEventListener("change", saveExportSettings);
  $("#exportCitationStyle").addEventListener("change", async () => {
    await saveExportSettings();
    const label = $("#exportCitationStyle").selectedOptions[0]?.textContent || "Citation style";
    setStatus(`${label} selected for exports`);
  });
  $("#promptExportFormat").addEventListener("change", loadPromptExportPlan);
  $("#createPromptExportPlan").addEventListener("click", createPromptExportPlan);
  $("#clearPromptExportPlan").addEventListener("click", clearPromptExportPlan);
  await loadStartupStep("Database", loadDatabaseInfo);
  await loadStartupStep("Summary", loadSummary);
  await loadStartupStep("CV import settings", loadCvImportSettings);
  await loadStartupStep("Connections", loadConnections);
  await loadStartupStep("Import inbox", loadImportInbox);
  await loadStartupStep("Profile sync", loadProfileSync);
  await loadStartupStep("Export settings", loadExportSettings);
  await loadStartupStep("Export formats", loadExportFormats);
  await loadStartupStep("Metrics", loadMetrics);
  await loadStartupStep("Collaboration map", loadCollaborationMap);
  await loadStartupStep("Journal metrics", loadJournalMetrics);
  await loadStartupStep("Ultrashort export profile", () => loadExportProfile("ultrashort"));
  await loadStartupStep("Short export profile", () => loadExportProfile("short"));
  await loadStartupStep("Biosketch", loadBiosketch);
  await loadStartupStep("Entries", loadEntries);
  await loadStartupStep("Person", loadPerson);
  await loadStartupStep("Narrative report", loadNarrativeReport);
  await loadStartupStep("R4RI contributions", loadR4riContributions);
  await loadStartupStep("Publications", loadPublications);
  await loadStartupStep("Onboarding", loadOnboarding);
  await resumeCloudBackgroundJob();
  handleOrcidOAuthResult();
  handleZoteroOAuthResult();
  window.setInterval(() => loadProfileSync().catch(() => {}), 5 * 60 * 1000);
  window.addEventListener("resize", () => {
    const config = ONBOARDING_STEPS[state.onboarding?.step];
    if (config) positionOnboardingCoach($(config.target), config);
  });
  window.addEventListener("scroll", () => {
    const config = ONBOARDING_STEPS[state.onboarding?.step];
    if (config) positionOnboardingCoach($(config.target), config);
  }, { passive: true });
}

init().catch((error) => setStatus(error.message));
