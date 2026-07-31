const $ = (selector) => document.querySelector(selector);

const elements = {
  authView: $("#authView"),
  libraryView: $("#libraryView"),
  accountNav: $("#accountNav"),
  accountIdentity: $("#accountIdentity"),
  authChoice: $("#authChoice"),
  loginTab: $("#loginTab"),
  inviteTab: $("#inviteTab"),
  loginForm: $("#loginForm"),
  inviteForm: $("#inviteForm"),
  registerForm: $("#registerForm"),
  loginMessage: $("#loginMessage"),
  inviteMessage: $("#inviteMessage"),
  registerMessage: $("#registerMessage"),
  logoutButton: $("#logoutButton"),
  databaseGrid: $("#databaseGrid"),
  emptyLibrary: $("#emptyLibrary"),
  libraryMessage: $("#libraryMessage"),
  newDatabaseButton: $("#newDatabaseButton"),
  uploadDatabaseButton: $("#uploadDatabaseButton"),
  databaseFileInput: $("#databaseFileInput"),
  profileStatus: $("#profileStatus"),
  profileStatusTitle: $("#profileStatusTitle"),
  profileStatusText: $("#profileStatusText"),
  viewProfileLink: $("#viewProfileLink"),
  profileSetupDialog: $("#profileSetupDialog"),
  profileSetupForm: $("#profileSetupForm"),
  profileDatabaseName: $("#profileDatabaseName"),
  profileSlug: $("#profileSlug"),
  profileSetupMessage: $("#profileSetupMessage"),
  closeProfileSetup: $("#closeProfileSetup"),
};
let libraryPollTimer = null;
let libraryPayload = null;
let profileDatabase = null;

function apiErrorMessage(payload, status) {
  const detail = payload?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (!item || typeof item !== "object") return "";
        const field = Array.isArray(item.loc) ? item.loc.at(-1) : "";
        const message = typeof item.msg === "string" ? item.msg : "";
        return [field && field !== "body" ? field : "", message].filter(Boolean).join(": ");
      })
      .filter(Boolean);
    if (messages.length) return messages.join(" · ");
  }
  if (detail && typeof detail === "object") {
    if (typeof detail.message === "string") return detail.message;
    if (typeof detail.msg === "string") return detail.msg;
  }
  return status === 422
    ? "Please check the highlighted information and try again."
    : "VitaMine could not complete that request.";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: options.body instanceof FormData
      ? options.headers
      : { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {}
  if (!response.ok) throw new Error(apiErrorMessage(payload, response.status));
  return payload;
}

function setBusy(form, busy) {
  for (const control of form.querySelectorAll("button, input")) control.disabled = busy;
}

function showAuthTab(name) {
  const login = name === "login";
  elements.loginTab.classList.toggle("active", login);
  elements.inviteTab.classList.toggle("active", !login);
  elements.loginForm.hidden = !login;
  elements.inviteForm.hidden = login;
  elements.loginMessage.textContent = "";
  elements.inviteMessage.textContent = "";
}

function showRegistration() {
  elements.authChoice.hidden = true;
  elements.registerForm.hidden = false;
  elements.registerForm.elements.email.focus();
}

function showAuth() {
  elements.accountNav.hidden = true;
  elements.libraryView.hidden = true;
  elements.authView.hidden = false;
}

function formatSize(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value) {
  if (!value) return "Not opened yet";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function suggestedProfileSlug(value) {
  return String(value || "researcher")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/g, "") || "researcher";
}

function closeProfileSetup() {
  if (elements.profileSetupDialog.open) elements.profileSetupDialog.close();
  profileDatabase = null;
  elements.profileSetupMessage.textContent = "";
}

function openProfileSetup(database) {
  const profile = libraryPayload?.profile;
  if (profile) {
    const changingSource = profile.source_database_id !== database.id;
    if (!changingSource) {
      window.location.assign(`/${encodeURIComponent(profile.slug)}`);
      return;
    }
    if (!window.confirm(`Use “${database.name}” as the source for your public profile instead?`)) return;
    publishProfileFromDatabase(database, profile.slug);
    return;
  }
  profileDatabase = database;
  elements.profileDatabaseName.textContent = database.name;
  elements.profileSlug.value = suggestedProfileSlug(libraryPayload?.account?.display_name);
  elements.profileSetupMessage.textContent = "";
  elements.profileSetupDialog.showModal();
  elements.profileSlug.focus();
  elements.profileSlug.select();
}

async function publishProfileFromDatabase(database, slug) {
  elements.libraryMessage.textContent = "Building your public profile…";
  try {
    const result = await api("/api/profile/publish", {
      method: "POST",
      body: JSON.stringify({ database_id: database.id, slug }),
    });
    window.location.assign(result.url);
  } catch (error) {
    elements.libraryMessage.textContent = error.message;
    elements.profileSetupMessage.textContent = error.message;
  }
}

function databaseCard(database, job = null) {
  const article = document.createElement("article");
  article.className = "database-card";
  const title = document.createElement("h2");
  title.textContent = database.name;
  const meta = document.createElement("p");
  meta.className = "database-meta";
  meta.textContent = `${formatSize(database.size_bytes)} · Last opened ${formatDate(database.last_opened_at)}`;
  const jobStatus = document.createElement("p");
  jobStatus.className = `background-job-status ${job?.status || ""}`;
  if (job) {
    const labels = {
      cv_import: "CV import",
      enrich_cv: "CV enrichment",
    };
    const progress = job.progress || {};
    const status = job.status === "succeeded"
      ? "finished — open to review"
      : job.status === "failed"
        ? `failed: ${job.error || "please open the CV for details"}`
        : progress.message || "continues in the background";
    jobStatus.textContent = `${labels[job.kind] || "Background process"} ${status}`;
  }
  const actions = document.createElement("div");
  actions.className = "database-actions";
  const open = document.createElement("button");
  open.className = "card-button open";
  open.textContent = "Open";
  open.addEventListener("click", () => openDatabase(database.id, open));
  const download = document.createElement("a");
  download.className = "card-button";
  download.textContent = "Download";
  download.href = `/api/account/databases/${encodeURIComponent(database.id)}/download`;
  if (job && ["queued", "running"].includes(job.status)) {
    download.classList.add("disabled");
    download.setAttribute("aria-disabled", "true");
    download.addEventListener("click", (event) => event.preventDefault());
  }
  const rename = document.createElement("button");
  rename.className = "card-button";
  rename.textContent = "Rename";
  rename.addEventListener("click", () => renameDatabase(database));
  const remove = document.createElement("button");
  remove.className = "card-button danger";
  remove.textContent = "Delete";
  remove.disabled = Boolean(job && ["queued", "running"].includes(job.status));
  remove.addEventListener("click", () => deleteDatabase(database));
  const profile = document.createElement("button");
  profile.className = "card-button profile";
  const currentProfile = libraryPayload?.profile;
  profile.textContent = currentProfile ? "Use for profile" : "Publish profile";
  profile.disabled = Boolean(job && ["queued", "running"].includes(job.status));
  profile.addEventListener("click", () => openProfileSetup(database));
  actions.append(open);
  if (currentProfile?.source_database_id !== database.id) actions.append(profile);
  actions.append(download, rename, remove);
  article.append(title, meta);
  if (job) article.append(jobStatus);
  article.append(actions);
  return article;
}

async function loadLibrary() {
  window.clearTimeout(libraryPollTimer);
  const [payload, jobsPayload] = await Promise.all([
    api("/api/account/databases"),
    api("/api/cloud/jobs"),
  ]);
  const jobs = new Map((jobsPayload.jobs || []).map((job) => [job.database_id, job]));
  libraryPayload = payload;
  elements.authView.hidden = true;
  elements.libraryView.hidden = false;
  elements.accountNav.hidden = false;
  elements.accountIdentity.textContent = payload.account.display_name || payload.account.email;
  elements.profileStatus.hidden = false;
  if (payload.profile) {
    elements.profileStatusTitle.textContent = `vitamine.cloud/${payload.profile.slug}`;
    elements.profileStatusText.textContent = "Synchronized with its source CV. Manage the layout, visibility, and embed options from the profile.";
    elements.viewProfileLink.href = `/${encodeURIComponent(payload.profile.slug)}`;
    elements.viewProfileLink.hidden = false;
  } else {
    elements.profileStatusTitle.textContent = "Share your academic profile.";
    elements.profileStatusText.textContent = "Choose Publish profile on a CV to create a bio, metrics, searchable publications, and collaborator map.";
    elements.viewProfileLink.hidden = true;
  }
  elements.databaseGrid.replaceChildren(
    ...payload.databases.map((database) => databaseCard(database, jobs.get(database.id))),
  );
  elements.emptyLibrary.hidden = payload.databases.length > 0;
  if ((jobsPayload.jobs || []).some((job) => ["queued", "running"].includes(job.status))) {
    libraryPollTimer = window.setTimeout(() => loadLibrary().catch(() => {}), 2000);
  }
}

async function openDatabase(databaseId, button) {
  button.disabled = true;
  elements.libraryMessage.textContent = "Opening your saved CV…";
  try {
    await api(`/gateway/databases/${encodeURIComponent(databaseId)}/open`, { method: "POST" });
    window.location.assign("/gateway/workspace/enter");
  } catch (error) {
    elements.libraryMessage.textContent = error.message;
    button.disabled = false;
  }
}

async function renameDatabase(database) {
  const name = window.prompt("Database name", database.name);
  if (!name || name.trim() === database.name) return;
  try {
    await api(`/api/account/databases/${encodeURIComponent(database.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
    elements.libraryMessage.textContent = "Database renamed.";
    await loadLibrary();
  } catch (error) {
    elements.libraryMessage.textContent = error.message;
  }
}

async function deleteDatabase(database) {
  if (!window.confirm(`Permanently delete “${database.name}” from your VitaMine account? Download a copy first if you may need it.`)) return;
  try {
    await api(`/api/account/databases/${encodeURIComponent(database.id)}`, { method: "DELETE" });
    elements.libraryMessage.textContent = "Database deleted.";
    await loadLibrary();
  } catch (error) {
    elements.libraryMessage.textContent = error.message;
  }
}

elements.loginTab.addEventListener("click", () => showAuthTab("login"));
elements.inviteTab.addEventListener("click", () => showAuthTab("invite"));

elements.inviteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setBusy(elements.inviteForm, true);
  elements.inviteMessage.textContent = "";
  try {
    const codeInput = elements.inviteForm.elements.namedItem("code");
    await api("/api/invitations/redeem", {
      method: "POST",
      body: JSON.stringify({ code: String(codeInput?.value || "").trim() }),
    });
    showRegistration();
  } catch (error) {
    elements.inviteMessage.textContent = error.message;
  } finally {
    setBusy(elements.inviteForm, false);
  }
});

elements.registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(elements.registerForm);
  const password = String(values.get("password") || "");
  elements.registerMessage.textContent = "";
  if (password !== values.get("password_confirmation")) {
    elements.registerMessage.textContent = "The passwords do not match.";
    return;
  }
  setBusy(elements.registerForm, true);
  try {
    await api("/api/account/register", {
      method: "POST",
      body: JSON.stringify({
        display_name: values.get("display_name"),
        email: values.get("email"),
        password,
      }),
    });
    await loadLibrary();
  } catch (error) {
    elements.registerMessage.textContent = error.message;
  } finally {
    setBusy(elements.registerForm, false);
  }
});

elements.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const emailInput = elements.loginForm.elements.namedItem("email");
  const passwordInput = elements.loginForm.elements.namedItem("password");
  const email = String(emailInput?.value || "").trim();
  const password = String(passwordInput?.value || "");
  setBusy(elements.loginForm, true);
  elements.loginMessage.textContent = "";
  try {
    await api("/api/account/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    await loadLibrary();
  } catch (error) {
    elements.loginMessage.textContent = error.message;
  } finally {
    setBusy(elements.loginForm, false);
  }
});

elements.logoutButton.addEventListener("click", async () => {
  try {
    await api("/api/account/logout", { method: "POST" });
  } finally {
    window.location.assign("/");
  }
});

elements.closeProfileSetup.addEventListener("click", closeProfileSetup);
$("#cancelProfileSetup").addEventListener("click", closeProfileSetup);
elements.profileSetupDialog.addEventListener("click", (event) => {
  if (event.target === elements.profileSetupDialog) closeProfileSetup();
});
elements.profileSlug.addEventListener("input", () => {
  elements.profileSlug.value = elements.profileSlug.value
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+/g, "")
    .slice(0, 40);
  elements.profileSetupMessage.textContent = "";
});
elements.profileSetupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!profileDatabase) return;
  const slug = suggestedProfileSlug(elements.profileSlug.value);
  if (!slug) return;
  setBusy(elements.profileSetupForm, true);
  await publishProfileFromDatabase(profileDatabase, slug);
  setBusy(elements.profileSetupForm, false);
});

elements.newDatabaseButton.addEventListener("click", async () => {
  elements.newDatabaseButton.disabled = true;
  elements.libraryMessage.textContent = "Creating your CV…";
  try {
    await api("/gateway/workspace/new", { method: "POST" });
    window.location.assign("/gateway/workspace/enter");
  } catch (error) {
    elements.libraryMessage.textContent = error.message;
    elements.newDatabaseButton.disabled = false;
  }
});

elements.uploadDatabaseButton.addEventListener("click", () => elements.databaseFileInput.click());
elements.databaseFileInput.addEventListener("change", async () => {
  const file = elements.databaseFileInput.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  elements.uploadDatabaseButton.disabled = true;
  elements.libraryMessage.textContent = "Uploading and checking your VitaMine database…";
  try {
    await api("/gateway/workspace/open", { method: "POST", body: form });
    window.location.assign("/gateway/workspace/enter");
  } catch (error) {
    elements.libraryMessage.textContent = error.message;
    elements.uploadDatabaseButton.disabled = false;
    elements.databaseFileInput.value = "";
  }
});

(async function initialize() {
  showAuth();
  try {
    const session = await api("/api/session");
    if (session.account) {
      await loadLibrary();
    } else {
      showRegistration();
    }
  } catch {
    showAuthTab("login");
  }
})();
