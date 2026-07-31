import sqlite3InitModule from "/assets/sqlite/index.mjs";

const state = {
  sqlite3: null,
  db: null,
  file: null,
  fileHandle: null,
  dirty: false,
  activePublicationId: null,
};

const elements = {
  welcome: document.querySelector("#welcome"),
  dashboard: document.querySelector("#dashboard"),
  openButton: document.querySelector("#openButton"),
  welcomeOpenButton: document.querySelector("#welcomeOpenButton"),
  saveButton: document.querySelector("#saveButton"),
  fileInput: document.querySelector("#fileInput"),
  engineStatus: document.querySelector("#engineStatus"),
  privacyStatus: document.querySelector("#privacyStatus"),
  workspaceMessage: document.querySelector("#workspaceMessage"),
  profileForm: document.querySelector("#profileForm"),
  profileMessage: document.querySelector("#profileMessage"),
  publicationForm: document.querySelector("#publicationForm"),
  publicationList: document.querySelector("#publicationList"),
  publicationEmpty: document.querySelector("#publicationEmpty"),
  publicationSearch: document.querySelector("#publicationSearch"),
  publicationMessage: document.querySelector("#publicationMessage"),
};

function rows(sql, bind = []) {
  return state.db.exec({
    sql,
    bind,
    rowMode: "object",
    returnValue: "resultRows",
  });
}

function scalar(sql, bind = []) {
  const result = rows(sql, bind);
  const first = result[0] || {};
  return Object.values(first)[0];
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function closeDatabase() {
  if (state.db) state.db.close();
  state.db = null;
  state.dirty = false;
  state.activePublicationId = null;
}

function validateDatabase() {
  const required = ["person", "cv_entries", "publications", "documents"];
  const tables = new Set(
    rows("SELECT name FROM sqlite_master WHERE type='table'").map((row) => row.name),
  );
  const missing = required.filter((table) => !tables.has(table));
  if (missing.length) {
    throw new Error(`This is not a VitaMine database. Missing tables: ${missing.join(", ")}.`);
  }
}

function safeScalar(sql, fallback = 0) {
  try {
    return scalar(sql) ?? fallback;
  } catch {
    return fallback;
  }
}

function updateSummary() {
  const person = rows(
    "SELECT full_name, display_name, degrees, position_title FROM person WHERE id=1",
  )[0] || {};
  const name = person.display_name || person.full_name || "Academic profile";
  const headline = [person.position_title, person.degrees].filter(Boolean).join(" · ");

  document.querySelector("#personName").textContent = name;
  document.querySelector("#personHeadline").textContent = headline;
  document.querySelector("#fileName").textContent = state.file.name;
  document.querySelector("#databaseFile").textContent = state.file.name;
  document.querySelector("#databaseSize").textContent = formatBytes(state.file.size);
  document.querySelector("#sqliteVersion").textContent = state.sqlite3.version.libVersion;
  document.querySelector("#publicationCount").textContent = safeScalar(
    "SELECT COUNT(*) FROM publications WHERE COALESCE(suppress_display, 0)=0",
  );
  document.querySelector("#entryCount").textContent = safeScalar("SELECT COUNT(*) FROM cv_entries");
  document.querySelector("#contributionCount").textContent = safeScalar(
    "SELECT COUNT(*) FROM biosketch_contributions",
  );
  document.querySelector("#sectionCount").textContent = safeScalar(
    "SELECT COUNT(DISTINCT section_key) FROM cv_entries",
  );
}

function markDirty(message = "Changes are ready to save locally.") {
  state.dirty = true;
  elements.saveButton.disabled = false;
  elements.saveButton.textContent = "Save locally •";
  elements.privacyStatus.textContent = "Unsaved local changes";
  elements.workspaceMessage.textContent = message;
}

function loadProfileForm() {
  const profile = rows(
    `SELECT full_name, display_name, degrees, position_title, office_address,
            work_email, work_phone, orcid_id, own_institution_name,
            own_institution_country
       FROM person WHERE id=1`,
  )[0] || {};
  for (const [name, value] of Object.entries(profile)) {
    const field = elements.profileForm.elements.namedItem(name);
    if (field) field.value = value || "";
  }
}

function publicationRows(search = "") {
  const term = `%${search.trim().toLowerCase()}%`;
  return rows(
    `SELECT id, title, authors, year, venue, category, doi, url, raw_citation
       FROM publications
      WHERE (? = '%%'
         OR lower(COALESCE(title, '')) LIKE ?
         OR lower(COALESCE(authors, '')) LIKE ?
         OR lower(COALESCE(venue, '')) LIKE ?)
      ORDER BY CASE WHEN year GLOB '[0-9][0-9][0-9][0-9]' THEN CAST(year AS INTEGER) ELSE 0 END DESC,
               COALESCE(ordinal, id) DESC
      LIMIT 750`,
    [term, term, term, term],
  );
}

function publicationMeta(publication) {
  return [publication.authors, publication.venue, publication.year]
    .filter(Boolean)
    .join(" · ");
}

function renderPublications() {
  const publications = publicationRows(elements.publicationSearch.value);
  elements.publicationList.replaceChildren();
  elements.publicationEmpty.hidden = publications.length !== 0;
  for (const publication of publications) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "publication-item";
    if (publication.id === state.activePublicationId) button.classList.add("active");
    button.dataset.publicationId = publication.id;
    const title = document.createElement("strong");
    title.textContent = publication.title || publication.raw_citation || "Untitled publication";
    const meta = document.createElement("span");
    meta.textContent = publicationMeta(publication);
    button.append(title, meta);
    button.addEventListener("click", () => editPublication(publication.id));
    elements.publicationList.append(button);
  }
}

function editPublication(publicationId) {
  const publication = rows(
    `SELECT id, title, authors, year, venue, category, doi, url, raw_citation
       FROM publications WHERE id=?`,
    [publicationId],
  )[0];
  if (!publication) return;
  state.activePublicationId = publication.id;
  elements.publicationForm.hidden = false;
  document.querySelector("#publicationEditorTitle").textContent = "Edit publication";
  document.querySelector("#deletePublicationButton").hidden = false;
  for (const [name, value] of Object.entries(publication)) {
    const field = elements.publicationForm.elements.namedItem(name);
    if (field) field.value = value || "";
  }
  elements.publicationMessage.textContent = "";
  renderPublications();
}

function newPublication() {
  state.activePublicationId = null;
  elements.publicationForm.reset();
  elements.publicationForm.elements.namedItem("id").value = "";
  elements.publicationForm.elements.namedItem("category").value = "peer_reviewed";
  elements.publicationForm.hidden = false;
  document.querySelector("#publicationEditorTitle").textContent = "Add publication";
  document.querySelector("#deletePublicationButton").hidden = true;
  elements.publicationMessage.textContent = "";
  renderPublications();
  elements.publicationForm.elements.namedItem("title").focus();
}

function showDashboard() {
  updateSummary();
  loadProfileForm();
  renderPublications();

  elements.welcome.hidden = true;
  elements.dashboard.hidden = false;
  elements.saveButton.disabled = false;
  elements.privacyStatus.textContent = "Local database open";
  elements.workspaceMessage.textContent = "";
}

function switchView(view) {
  for (const button of document.querySelectorAll("[data-view]")) {
    button.classList.toggle("active", button.dataset.view === view);
  }
  for (const panel of document.querySelectorAll("[data-view-panel]")) {
    const active = panel.dataset.viewPanel === view;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  }
}

async function loadFile(file, handle = null) {
  if (!state.sqlite3) throw new Error("The local database engine is still loading.");
  if (!file.name.match(/\.(vitamine|sqlite|db)$/i)) {
    throw new Error("Choose a .vitamine, .sqlite, or .db file.");
  }
  const bytes = await file.arrayBuffer();
  closeDatabase();
  const pointer = state.sqlite3.wasm.allocFromTypedArray(bytes);
  const db = new state.sqlite3.oo1.DB();
  const flags =
    state.sqlite3.capi.SQLITE_DESERIALIZE_FREEONCLOSE |
    state.sqlite3.capi.SQLITE_DESERIALIZE_RESIZEABLE;
  const result = state.sqlite3.capi.sqlite3_deserialize(
    db.pointer,
    "main",
    pointer,
    bytes.byteLength,
    Math.max(bytes.byteLength * 2, bytes.byteLength + 64 * 1024 * 1024),
    flags,
  );
  db.checkRc(result);
  state.db = db;
  state.file = file;
  state.fileHandle = handle;
  validateDatabase();
  showDashboard();
}

async function chooseFile() {
  elements.workspaceMessage.textContent = "";
  try {
    if (state.dirty && !window.confirm("Open another file and discard the unsaved local changes?")) {
      return;
    }
    if ("showOpenFilePicker" in window) {
      const [handle] = await window.showOpenFilePicker({
        multiple: false,
        types: [{
          description: "VitaMine database",
          accept: { "application/vnd.sqlite3": [".vitamine", ".sqlite", ".db"] },
        }],
      });
      await loadFile(await handle.getFile(), handle);
    } else {
      elements.fileInput.click();
    }
  } catch (error) {
    if (error.name !== "AbortError") {
      elements.workspaceMessage.textContent = error.message;
      elements.engineStatus.textContent = error.message;
    }
  }
}

async function saveDatabase() {
  if (!state.db) return;
  const bytes = state.sqlite3.capi.sqlite3_js_db_export(state.db);
  const blob = new Blob([bytes], { type: "application/vnd.sqlite3" });
  try {
    if (state.fileHandle?.createWritable) {
      const writable = await state.fileHandle.createWritable();
      await writable.write(blob);
      await writable.close();
      state.dirty = false;
      elements.saveButton.textContent = "Save locally";
      elements.privacyStatus.textContent = "Local database saved";
      elements.workspaceMessage.textContent = `Saved ${state.file.name} locally.`;
      return;
    }
    if ("showSaveFilePicker" in window) {
      const handle = await window.showSaveFilePicker({
        suggestedName: state.file.name,
        types: [{
          description: "VitaMine database",
          accept: { "application/vnd.sqlite3": [".vitamine"] },
        }],
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      state.fileHandle = handle;
      state.dirty = false;
      elements.saveButton.textContent = "Save locally";
      elements.privacyStatus.textContent = "Local database saved";
      elements.workspaceMessage.textContent = `Saved ${state.file.name} locally.`;
      return;
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = state.file.name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    state.dirty = false;
    elements.saveButton.textContent = "Save locally";
    elements.privacyStatus.textContent = "Local copy downloaded";
    elements.workspaceMessage.textContent = "Saved a local copy to your Downloads folder.";
  } catch (error) {
    if (error.name !== "AbortError") elements.workspaceMessage.textContent = error.message;
  }
}

elements.openButton.addEventListener("click", chooseFile);
elements.welcomeOpenButton.addEventListener("click", chooseFile);
elements.saveButton.addEventListener("click", saveDatabase);
for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => switchView(button.dataset.view));
}

elements.profileForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const values = new FormData(elements.profileForm);
  state.db.exec({
    sql: `UPDATE person
             SET full_name=?, display_name=?, degrees=?, position_title=?,
                 office_address=?, work_email=?, work_phone=?, orcid_id=?,
                 own_institution_name=?, own_institution_country=?
           WHERE id=1`,
    bind: [
      values.get("full_name"),
      values.get("display_name"),
      values.get("degrees"),
      values.get("position_title"),
      values.get("office_address"),
      values.get("work_email"),
      values.get("work_phone"),
      values.get("orcid_id"),
      values.get("own_institution_name"),
      values.get("own_institution_country"),
    ],
  });
  updateSummary();
  markDirty("Profile changes are ready to save locally.");
  elements.profileMessage.textContent = "Applied to the open database.";
});

elements.publicationSearch.addEventListener("input", renderPublications);
document.querySelector("#newPublicationButton").addEventListener("click", newPublication);
document.querySelector("#closePublicationButton").addEventListener("click", () => {
  elements.publicationForm.hidden = true;
  state.activePublicationId = null;
  renderPublications();
});

elements.publicationForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const values = new FormData(elements.publicationForm);
  const title = String(values.get("title") || "").trim();
  const citation = String(values.get("raw_citation") || "").trim() || title;
  const bind = [
    title,
    values.get("authors"),
    values.get("year"),
    values.get("venue"),
    values.get("category") || "peer_reviewed",
    values.get("doi"),
    values.get("url"),
    citation,
  ];
  if (state.activePublicationId) {
    state.db.exec({
      sql: `UPDATE publications
               SET title=?, authors=?, year=?, venue=?, category=?, doi=?, url=?, raw_citation=?
             WHERE id=?`,
      bind: [...bind, state.activePublicationId],
    });
  } else {
    state.db.exec({
      sql: `INSERT INTO publications
              (source, title, authors, year, venue, category, doi, url, raw_citation, confidence)
            VALUES ('manual', ?, ?, ?, ?, ?, ?, ?, ?, 'high')`,
      bind,
    });
    state.activePublicationId = Number(scalar("SELECT last_insert_rowid()"));
  }
  updateSummary();
  renderPublications();
  markDirty("Publication changes are ready to save locally.");
  elements.publicationMessage.textContent = "Applied to the open database.";
  document.querySelector("#deletePublicationButton").hidden = false;
  document.querySelector("#publicationEditorTitle").textContent = "Edit publication";
});

document.querySelector("#deletePublicationButton").addEventListener("click", () => {
  if (!state.activePublicationId) return;
  const title = elements.publicationForm.elements.namedItem("title").value || "this publication";
  if (!window.confirm(`Delete “${title}” from the open database?`)) return;
  state.db.exec({ sql: "DELETE FROM publications WHERE id=?", bind: [state.activePublicationId] });
  state.activePublicationId = null;
  elements.publicationForm.hidden = true;
  updateSummary();
  renderPublications();
  markDirty("The deletion is ready to save locally.");
});

elements.fileInput.addEventListener("change", async () => {
  const file = elements.fileInput.files[0];
  if (!file) return;
  try {
    await loadFile(file);
  } catch (error) {
    elements.engineStatus.textContent = error.message;
  } finally {
    elements.fileInput.value = "";
  }
});

window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

try {
  state.sqlite3 = await sqlite3InitModule({
    print: () => {},
    printErr: console.error,
  });
  elements.engineStatus.textContent = `Local SQLite ${state.sqlite3.version.libVersion} is ready.`;
} catch (error) {
  console.error(error);
  elements.engineStatus.textContent = "The local database engine could not start in this browser.";
  elements.openButton.disabled = true;
  elements.welcomeOpenButton.disabled = true;
}
