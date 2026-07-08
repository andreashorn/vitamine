const state = {
  sections: {},
  entries: [],
  publications: [],
  journalMetrics: [],
  identifiers: [],
  exportProfiles: {
    short: { selected: [], candidates: [], settings: {} },
    ultrashort: { selected: [], candidates: [], settings: {} },
  },
  biosketch: {
    contributions: [],
    publication_count: 0,
    contribution_limit: 5,
    products_per_contribution_limit: 4,
    publication_limit: 20,
  },
  selectedEntry: null,
  publicationSort: { key: "year", direction: "desc" },
  draggedPublicationId: null,
  draggedDropProfile: null,
  draggedDropId: null,
  draggedBiosketchContributionId: null,
  draggedBiosketchPublicationId: null,
  selectedBiosketchContributionId: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function setStatus(text) {
  $("#status").textContent = text;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.stderr || data.detail || "Request failed");
  }
  return data;
}

function setActionButtons(disabled) {
  [
    "#syncZoteroDashboard",
    "#syncOrcidDashboard",
    "#enrichDoiDashboard",
    "#maintainPubsDashboard",
    "#fetchJournalMetrics",
    "#saveJournalMetrics",
    "#useExampleDatabase",
    "#createBlankDatabase",
    "#buildUltraDashboard",
    "#buildShortDashboard",
    "#buildLongDashboard",
    "#buildBiosketchDashboard",
    "#newBiosketchAchievement",
    "#deleteBiosketchAchievement",
  ].forEach((selector) => {
    const button = $(selector);
    if (button) button.disabled = disabled;
  });
}

function fillSectionSelects() {
  const options = ['<option value="">All sections</option>']
    .concat(Object.entries(state.sections).map(([key, label]) => `<option value="${key}">${label}</option>`))
    .join("");
  $("#sectionFilter").innerHTML = options;
  $("#entrySection").innerHTML = Object.entries(state.sections)
    .map(([key, label]) => `<option value="${key}">${label}</option>`)
    .join("");
}

async function loadSummary() {
  const data = await api("/api/summary");
  state.sections = data.sections;
  fillSectionSelects();
  $("#summaryGrid").innerHTML = [
    summaryBox("Entries", data.entries.map((row) => `${state.sections[row.section_key] || row.section_key}: ${row.count}`)),
    summaryBox("Publications", data.publications.map((row) => `${row.source} / ${row.category}: ${row.count}`)),
    summaryBox("Warnings", data.warnings.map((row) => `${row.warning_type}: ${row.count}`)),
  ].join("");
}

async function loadDatabaseInfo() {
  const data = await api("/api/database");
  const label = data.is_example ? `${data.active_name} (example)` : data.active_name;
  $("#databaseName").textContent = label;
  $("#databaseName").title = data.active || "";
}

async function useExampleDatabase() {
  await api("/api/database/use-example", { method: "POST" });
  setStatus("Example database loaded");
  window.location.reload();
}

async function createBlankDatabase() {
  const name = window.prompt("Name for the new database", "workspace");
  if (name === null) return;
  await api("/api/database/create", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  setStatus("Blank database created");
  window.location.reload();
}

async function loadMetrics() {
  const data = await api("/api/metrics");
  const pubs = data.publications || {};
  $("#metricsGrid").innerHTML = [
    metricCard("Visible pubs", pubs.visible || 0),
    metricCard("Peer reviewed", pubs.peer_reviewed || 0),
    metricCard("Short selected", pubs.selected_short || 0),
    metricCard("Ultrashort", pubs.selected_ultrashort || 0),
    metricCard("ORCID matched", pubs.orcid_matched || 0),
    metricCard("Citation metrics", pubs.citation_metric_count || 0),
    metricCard("OpenAlex cites", pubs.openalex_cited_by_total || 0),
    metricCard("Impact factors", pubs.impact_factor_count || 0),
    metricCard("Hidden/problem", pubs.suppressed || 0),
    metricCard("Missing year", pubs.missing_year || 0),
    metricCard("Missing DOI", pubs.missing_doi || 0),
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
}

function metricCard(label, value) {
  return `<div class="metricCard"><strong>${value}</strong><span>${label}</span></div>`;
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
  $(target).textContent = lines.filter(Boolean).join("\n") || "Done.";
}

function refreshExportLinks(data) {
  if (data.html || data.pdf) {
    if (data.html && data.html.includes("long_cv")) {
      const link = $("#openLongDashboardHtml");
      if (link) link.href = data.html;
    }
    if (data.pdf && data.pdf.includes("long_cv")) {
      const link = $("#openLongDashboardPdf");
      if (link) link.href = data.pdf;
    }
    $$("a[href*='long_cv.html']").forEach((link) => {
      if (data.html && data.html.includes("long_cv")) link.href = data.html;
    });
    $$("a[href*='long_cv.pdf']").forEach((link) => {
      if (data.pdf && data.pdf.includes("long_cv")) link.href = data.pdf;
    });
    $$("a[href*='short_cv.html']").forEach((link) => {
      if (data.html && data.html.includes("short_cv")) link.href = data.html;
    });
    $$("a[href*='short_cv.pdf']").forEach((link) => {
      if (data.pdf && data.pdf.includes("short_cv")) link.href = data.pdf;
    });
    $$("a[href*='biosketch.html']").forEach((link) => {
      if (data.html && data.html.includes("biosketch")) link.href = data.html;
    });
    $$("a[href*='biosketch.pdf']").forEach((link) => {
      if (data.pdf && data.pdf.includes("biosketch")) link.href = data.pdf;
    });
  }
  if (data.docx) {
    $$("a[href*='ultrashort_tabular_cv.docx']").forEach((link) => {
      link.href = data.docx;
    });
  }
}

function openBuiltArtifact(data) {
  const href = data.pdf || data.docx || data.html;
  if (!href) return;
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
        <td>${entry.title || ""}</td>
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

async function saveEntry(event) {
  event.preventDefault();
  const id = $("#entryId").value;
  const method = id ? "PUT" : "POST";
  const path = id ? `/api/entries/${id}` : "/api/entries";
  await api(path, { method, body: JSON.stringify(entryPayload()) });
  setStatus("Entry saved");
  await loadEntries();
  await loadSummary();
}

async function deleteEntry() {
  const id = $("#entryId").value;
  if (!id) return;
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
  await loadPersonIdentifiers();
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
  $("#identifierId").value = "";
  $("#identifierPlatform").value = "";
  $("#identifierType").value = "";
  $("#identifierValue").value = "";
  $("#identifierUrl").value = "";
  $("#identifierNotes").value = "";
}

function editIdentifier(id) {
  const row = state.identifiers.find((identifier) => identifier.id === id);
  if (!row) return;
  $("#identifierId").value = row.id;
  $("#identifierPlatform").value = row.platform || "";
  $("#identifierType").value = row.identifier_type || "";
  $("#identifierValue").value = row.identifier_value || "";
  $("#identifierUrl").value = row.url || "";
  $("#identifierNotes").value = row.notes || "";
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

async function saveIdentifier(event) {
  event.preventDefault();
  const id = $("#identifierId").value;
  const path = id ? `/api/person/identifiers/${id}` : "/api/person/identifiers";
  const method = id ? "PUT" : "POST";
  await api(path, { method, body: JSON.stringify(identifierPayload()) });
  setStatus("Identifier saved");
  clearIdentifierForm();
  await loadPersonIdentifiers();
}

async function deleteIdentifier() {
  const id = $("#identifierId").value;
  if (!id) return;
  await api(`/api/person/identifiers/${id}`, { method: "DELETE" });
  setStatus("Identifier deleted");
  clearIdentifierForm();
  await loadPersonIdentifiers();
}

async function savePerson(event) {
  event.preventDefault();
  const payload = {};
  new FormData($("#personForm")).forEach((value, key) => {
    payload[key] = value;
  });
  await api("/api/person", { method: "PUT", body: JSON.stringify(payload) });
  setStatus("Person saved");
}

async function loadNarrativeReport() {
  const report = await api("/api/narrative-report");
  $("#narrativeTitle").value = report.title || "Narrative Report";
  $("#narrativeBody").value = report.body || "";
  $("#narrativeTitleDe").value = report.title_de || "Narrativer Bericht";
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

async function loadPublications() {
  const params = new URLSearchParams();
  const q = $("#pubSearch").value.trim();
  if (q) params.set("q", q);
  if ($("#showSuppressedPubs").checked) params.set("show_suppressed", "1");
  params.set("sort", state.publicationSort.key);
  params.set("direction", state.publicationSort.direction);
  const data = await api(`/api/publications?${params.toString()}`);
  state.publications = data.publications;
  $("#publicationsBody").innerHTML = data.publications
    .map((pub) => `<tr class="${pub.suppress_display ? "mutedRow" : ""}" draggable="true" data-publication-id="${pub.id}">
      <td>${escapeHtml(pub.year || "")}</td>
      <td>${publicationSource(pub)}</td>
      <td>${publicationFlags(pub)}</td>
      <td>${escapeHtml(pub.selected_order || "")}</td>
      <td>${escapeHtml(pub.title || "")}${pub.quality_note ? `<div class="qualityNote">${escapeHtml(pub.quality_note)}</div>` : ""}</td>
      <td>${escapeHtml(pub.venue || "")}</td>
      <td>${impactFactor(pub)}</td>
      <td>${escapeHtml(pub.doi || "")}</td>
    </tr>`)
    .join("");
  $$("#publicationsBody tr[data-publication-id]").forEach((row) => {
    row.addEventListener("dragstart", (event) => {
      state.draggedPublicationId = Number(row.dataset.publicationId);
      state.draggedDropProfile = null;
      state.draggedDropId = null;
      clearBiosketchDragState();
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("text/plain", String(state.draggedPublicationId));
    });
  });
  renderPublicationSortIndicators();
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
  const container = $("#biosketchAchievements");
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
  count.title = `NIH legacy biosketch: up to ${contributionLimit} contributions, with up to ${productsPerContributionLimit} cited products each.`;
  count.classList.toggle("dangerCount", total > limit || contributionOverLimit || productOverLimit);
  container.innerHTML = contributions.length
    ? contributions.map((contribution) => biosketchAchievementMarkup(contribution)).join("")
    : `<p class="emptyState">No achievements yet.</p>`;
  wireBiosketchEditor();
}

function biosketchAchievementMarkup(contribution) {
  const pubs = contribution.publications || [];
  const selected = state.selectedBiosketchContributionId === contribution.id ? " selected" : "";
  const perContributionLimit = state.biosketch.products_per_contribution_limit || 4;
  const overLimit = pubs.length > perContributionLimit ? " overLimit" : "";
  return `
    <article class="biosketchAchievement${selected}${overLimit}" data-contribution-id="${contribution.id}">
      <div class="biosketchAchievementHeader">
        <span>${escapeHtml(String(contribution.ordinal || ""))}</span>
        <input class="biosketchTitle" value="${escapeHtml(contribution.title || "")}" aria-label="Achievement title">
      </div>
      <textarea class="biosketchNarrative" rows="5" aria-label="Achievement text">${escapeHtml(contribution.narrative || "")}</textarea>
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
  $$(".biosketchAchievement").forEach((achievement) => {
    const contributionId = Number(achievement.dataset.contributionId);
    const title = achievement.querySelector(".biosketchTitle");
    const narrative = achievement.querySelector(".biosketchNarrative");
    achievement.addEventListener("click", () => {
      if (state.selectedBiosketchContributionId === contributionId) return;
      state.selectedBiosketchContributionId = contributionId;
      $$(".biosketchAchievement").forEach((item) => item.classList.remove("selected"));
      achievement.classList.add("selected");
    });
    const saveText = debounce(() => saveBiosketchAchievementText(contributionId, title.value, narrative.value), 650);
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

async function saveBiosketchAchievementText(contributionId, title, narrative) {
  if (!title.trim()) return;
  await api(`/api/biosketch/contributions/${contributionId}`, {
    method: "PUT",
    body: JSON.stringify({ title, narrative }),
  });
  setStatus("Biosketch achievement saved");
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

async function createBiosketchAchievement() {
  const data = await api("/api/biosketch/contributions", {
    method: "POST",
    body: JSON.stringify({ title: "New Achievement", narrative: "" }),
  });
  state.selectedBiosketchContributionId = data.id;
  setStatus("Biosketch achievement created");
  await loadBiosketch();
}

async function deleteBiosketchAchievement() {
  const contributions = state.biosketch.contributions || [];
  const contribution =
    contributions.find((item) => item.id === state.selectedBiosketchContributionId) || contributions[contributions.length - 1];
  if (!contribution) return;
  await api(`/api/biosketch/contributions/${contribution.id}`, { method: "DELETE" });
  state.selectedBiosketchContributionId = null;
  setStatus("Biosketch achievement deleted");
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

async function runAction(path, doneText, workingText = "Working...") {
  setStatus(workingText);
  setActionButtons(true);
  try {
    const data = await api(path, { method: "POST" });
    setStatus(doneText);
    await loadSummary();
    await loadMetrics();
    await loadPublications();
    return data;
  } catch (error) {
    setStatus(error.message);
    throw error;
  } finally {
    setActionButtons(false);
  }
}

function switchView(name) {
  $$(".tab").forEach((tab) => {
    const selected = tab.dataset.view === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", selected ? "true" : "false");
    tab.tabIndex = selected ? 0 : -1;
  });
  $$(".view").forEach((view) => {
    const selected = view.id === `${name}View`;
    view.classList.toggle("active", selected);
    view.hidden = !selected;
  });
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

async function init() {
  $$(".tab[data-view]").forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
    tab.addEventListener("keydown", handleTabKeydown);
  });
  switchView("dashboard");
  $("#sectionFilter").addEventListener("change", loadEntries);
  $("#entrySearch").addEventListener("input", debounce(loadEntries));
  $("#pubSearch").addEventListener("input", debounce(loadPublications));
  $("#showSuppressedPubs").addEventListener("change", loadPublications);
  $("#journalMetricSearch").addEventListener("input", debounce(loadJournalMetrics));
  $("#saveJournalMetrics").addEventListener("click", saveJournalMetrics);
  $("#useExampleDatabase").addEventListener("click", useExampleDatabase);
  $("#createBlankDatabase").addEventListener("click", createBlankDatabase);
  $("#newBiosketchAchievement").addEventListener("click", createBiosketchAchievement);
  $("#deleteBiosketchAchievement").addEventListener("click", deleteBiosketchAchievement);
  $$("#publicationsView th[data-sort]").forEach((header) => {
    header.addEventListener("click", () => sortPublicationsBy(header.dataset.sort));
  });
  $("#newEntry").addEventListener("click", clearEntryForm);
  $("#entryForm").addEventListener("submit", saveEntry);
  $("#deleteEntry").addEventListener("click", deleteEntry);
  $("#personForm").addEventListener("submit", savePerson);
  $("#identifierForm").addEventListener("submit", saveIdentifier);
  $("#newIdentifier").addEventListener("click", clearIdentifierForm);
  $("#deleteIdentifier").addEventListener("click", deleteIdentifier);
  $("#narrativeForm").addEventListener("submit", saveNarrativeReport);
  const sync = async () => {
    $("#syncOutput").textContent = "Syncing Zotero...";
    const data = await runAction("/api/actions/sync-zotero", "Zotero synced", "Syncing Zotero...");
    actionLog("#syncOutput", data);
  };
  const buildUltra = async () => {
    $("#exportOutput").textContent = "Building one-page CV...";
    const data = await runAction("/api/actions/build-ultrashort-tabular", "One-page CV built", "Building one-page CV...");
    refreshExportLinks(data);
    actionLog("#exportOutput", data);
    openBuiltArtifact(data);
  };
  const buildLong = async () => {
    const language = $("#longCvLanguage").value || "en";
    $("#exportOutput").textContent = `Building long CV (${language})...`;
    const data = await runAction(`/api/actions/build-long?lang=${encodeURIComponent(language)}`, "Long CV built", "Building long CV...");
    refreshExportLinks(data);
    actionLog("#exportOutput", data);
    openBuiltArtifact(data);
  };
  const buildShort = async () => {
    $("#exportOutput").textContent = "Building short CV...";
    const data = await runAction("/api/actions/build-short", "Short CV built", "Building short CV...");
    refreshExportLinks(data);
    actionLog("#exportOutput", data);
    openBuiltArtifact(data);
  };
  const buildBiosketch = async () => {
    $("#exportOutput").textContent = "Building biosketch...";
    const data = await runAction("/api/actions/build-biosketch", "Biosketch built", "Building biosketch...");
    refreshExportLinks(data);
    actionLog("#exportOutput", data);
    openBuiltArtifact(data);
  };
  const maintainPubs = async () => {
    $("#syncOutput").textContent = "Cleaning publication rows and applying journal metrics...";
    const data = await runAction("/api/actions/maintain-publications", "Publication maintenance complete", "Maintaining publications...");
    actionLog("#syncOutput", data);
  };
  const fetchJournalMetrics = async () => {
    $("#syncOutput").textContent = "Fetching OpenAlex journal metrics...";
    const data = await runAction("/api/actions/fetch-journal-metrics", "OpenAlex journal metrics fetched", "Fetching OpenAlex journal metrics...");
    actionLog("#syncOutput", data);
    await loadJournalMetrics();
  };
  const enrichDoi = async () => {
    $("#syncOutput").textContent = "Enriching DOI metadata from Crossref, PubMed, and OpenAlex...";
    const data = await runAction("/api/actions/enrich-doi", "DOI metadata enriched", "Enriching DOI metadata...");
    actionLog("#syncOutput", data);
  };
  const syncOrcid = async () => {
    $("#syncOutput").textContent = "Syncing ORCID public works...";
    const data = await runAction("/api/actions/sync-orcid", "ORCID synced", "Syncing ORCID...");
    actionLog("#syncOutput", data);
  };
  $("#syncZoteroDashboard").addEventListener("click", sync);
  $("#syncOrcidDashboard").addEventListener("click", syncOrcid);
  $("#enrichDoiDashboard").addEventListener("click", enrichDoi);
  $("#maintainPubsDashboard").addEventListener("click", maintainPubs);
  $("#fetchJournalMetrics").addEventListener("click", fetchJournalMetrics);
  $("#buildUltraDashboard").addEventListener("click", buildUltra);
  $("#buildShortDashboard").addEventListener("click", buildShort);
  $("#buildLongDashboard").addEventListener("click", buildLong);
  $("#buildBiosketchDashboard").addEventListener("click", buildBiosketch);
  await loadSummary();
  await loadDatabaseInfo();
  await loadMetrics();
  await loadJournalMetrics();
  await loadExportProfile("ultrashort");
  await loadExportProfile("short");
  await loadBiosketch();
  await loadEntries();
  await loadPerson();
  await loadNarrativeReport();
  await loadPublications();
}

init().catch((error) => setStatus(error.message));
