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
  verificationPending: $("#verificationPending"),
  verificationEmail: $("#verificationEmail"),
  verificationMessage: $("#verificationMessage"),
  resendVerification: $("#resendVerification"),
  passkeyLoginButton: $("#passkeyLoginButton"),
  forgotPasswordButton: $("#forgotPasswordButton"),
  passwordResetRequestForm: $("#passwordResetRequestForm"),
  passwordResetRequestMessage: $("#passwordResetRequestMessage"),
  passwordResetForm: $("#passwordResetForm"),
  passwordResetMessage: $("#passwordResetMessage"),
  addPasskeyButton: $("#addPasskeyButton"),
  passkeyMessage: $("#passkeyMessage"),
  passkeyStatus: $("#passkeyStatus"),
  settingsButton: $("#settingsButton"),
  settingsDialog: $("#settingsDialog"),
  closeSettings: $("#closeSettings"),
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
  plusStatusButton: $("#plusStatusButton"),
  plusDialog: $("#plusDialog"),
  plusDialogTitle: $("#plusDialogTitle"),
  plusDialogStatus: $("#plusDialogStatus"),
  closePlusDialog: $("#closePlusDialog"),
  plusDeveloperSetting: $("#plusDeveloperSetting"),
  plusDeveloperToggle: $("#plusDeveloperToggle"),
};
let libraryPollTimer = null;
let libraryPayload = null;
let profileDatabase = null;
let conditionalPasskeyController = null;

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

function showVerificationPending(email) {
  elements.authChoice.hidden = true;
  elements.registerForm.hidden = true;
  elements.verificationPending.hidden = false;
  elements.verificationEmail.textContent = email;
}

function showPasswordResetRequest() {
  elements.authChoice.hidden = true;
  elements.registerForm.hidden = true;
  elements.verificationPending.hidden = true;
  elements.passwordResetForm.hidden = true;
  elements.passwordResetRequestForm.hidden = false;
  const loginEmail = elements.loginForm.elements.namedItem("email")?.value || "";
  elements.passwordResetRequestForm.elements.namedItem("email").value = loginEmail;
  elements.passwordResetRequestForm.elements.namedItem("email").focus();
}

function returnToLogin() {
  elements.passwordResetRequestForm.hidden = true;
  elements.passwordResetForm.hidden = true;
  elements.verificationPending.hidden = true;
  elements.registerForm.hidden = true;
  elements.authChoice.hidden = false;
  showAuthTab("login");
}

function showAuth() {
  elements.accountNav.hidden = true;
  elements.libraryView.hidden = true;
  elements.authView.hidden = false;
}

function bytesFromBase64url(value) {
  const base64 = String(value).replaceAll("-", "+").replaceAll("_", "/");
  const binary = atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, "="));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function base64urlFromBytes(value) {
  const bytes = new Uint8Array(value || []);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function browserCredentialOptions(options) {
  const converted = structuredClone(options);
  converted.challenge = bytesFromBase64url(converted.challenge);
  if (converted.user?.id) converted.user.id = bytesFromBase64url(converted.user.id);
  for (const descriptor of converted.excludeCredentials || []) descriptor.id = bytesFromBase64url(descriptor.id);
  for (const descriptor of converted.allowCredentials || []) descriptor.id = bytesFromBase64url(descriptor.id);
  return converted;
}

function credentialPayload(credential) {
  const response = credential.response;
  const payload = {
    id: credential.id,
    rawId: base64urlFromBytes(credential.rawId),
    type: credential.type,
    response: { clientDataJSON: base64urlFromBytes(response.clientDataJSON) },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
  if (response.attestationObject) {
    payload.response.attestationObject = base64urlFromBytes(response.attestationObject);
    payload.response.transports = response.getTransports?.() || [];
  } else {
    payload.response.authenticatorData = base64urlFromBytes(response.authenticatorData);
    payload.response.signature = base64urlFromBytes(response.signature);
    payload.response.userHandle = response.userHandle ? base64urlFromBytes(response.userHandle) : null;
  }
  return payload;
}

async function createPasskey(messageElement = elements.passkeyMessage) {
  if (!window.PublicKeyCredential) throw new Error("Passkeys are not supported by this browser.");
  const request = await api("/api/account/passkeys/register/options", { method: "POST" });
  const credential = await navigator.credentials.create({ publicKey: browserCredentialOptions(request.options) });
  if (!credential) throw new Error("Passkey creation was cancelled.");
  await api("/api/account/passkeys/register/complete", {
    method: "POST",
    body: JSON.stringify({ challenge_id: request.challenge_id, credential: credentialPayload(credential) }),
  });
  messageElement.textContent = "Passkey added. You can now sign in without entering your email or password.";
}

async function signInWithPasskey({ conditional = false } = {}) {
  if (!window.PublicKeyCredential) throw new Error("Passkeys are not supported by this browser.");
  const request = await api("/api/account/passkeys/login/options", {
    method: "POST",
    body: JSON.stringify({}),
  });
  const credential = await navigator.credentials.get({
    publicKey: browserCredentialOptions(request.options),
    ...(conditional ? { mediation: "conditional" } : {}),
    ...(conditionalPasskeyController ? { signal: conditionalPasskeyController.signal } : {}),
  });
  if (!credential) throw new Error("Passkey sign-in was cancelled.");
  await api("/api/account/passkeys/login/complete", {
    method: "POST",
    body: JSON.stringify({ challenge_id: request.challenge_id, credential: credentialPayload(credential) }),
  });
  await loadLibrary();
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

function renderPlusStatus(plus = {}) {
  elements.plusStatusButton.textContent = plus.active ? "VitaMine+" : "Upgrade to +";
  elements.plusStatusButton.classList.toggle("active", Boolean(plus.active));
  elements.plusDeveloperSetting.hidden = !plus.developer_toggle;
  if (plus.developer_toggle) elements.plusDeveloperToggle.checked = Boolean(plus.active);
  if (plus.plan === "developer") {
    elements.plusDialogTitle.textContent = plus.active ? "Developer VitaMine+ access is enabled." : "Developer free-plan mode is enabled.";
    elements.plusDialogStatus.textContent = "Use the temporary switch in Settings to move between both plan experiences.";
  } else if (plus.plan === "trial") {
    elements.plusDialogTitle.textContent = "Your VitaMine+ trial is active.";
    elements.plusDialogStatus.textContent = `All VitaMine+ features are available until ${formatDate(plus.active_until)}. After that, VitaMine remains free and your data stays accessible.`;
  } else if (plus.plan === "paid") {
    elements.plusDialogTitle.textContent = "Your VitaMine+ plan is active.";
    elements.plusDialogStatus.textContent = plus.active_until ? `Your current plan runs through ${formatDate(plus.active_until)}.` : "All VitaMine+ features are available.";
  } else {
    elements.plusDialogTitle.textContent = "More confidence, less maintenance.";
    elements.plusDialogStatus.textContent = "VitaMine stays free. Upgrade to keep intelligent import, enrichment, custom-template, and network-exploration features.";
  }
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
  download.className = "card-button download";
  download.textContent = "Download data (.vitamine)";
  download.title = "Download this CV's portable SQLite database";
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
  const canSelectForProfile = currentProfile?.source_database_id !== database.id;
  profile.textContent = currentProfile ? "Use for profile" : "Publish profile";
  profile.disabled = Boolean(job && ["queued", "running"].includes(job.status));
  profile.addEventListener("click", () => openProfileSetup(database));
  actions.append(open);
  if (canSelectForProfile) {
    actions.classList.add("has-profile-action");
    actions.append(profile);
  }
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
  renderPlusStatus(payload.plus);
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
    const result = await api("/api/account/register", {
      method: "POST",
      body: JSON.stringify({
        display_name: values.get("display_name"),
        email: values.get("email"),
        password,
      }),
    });
    if (values.get("set_up_passkey")) {
      elements.registerMessage.textContent = "Your account is ready. Finish setting up your passkey…";
      try {
        await createPasskey(elements.registerMessage);
      } catch (error) {
        showVerificationPending(result.account.email);
        elements.verificationMessage.textContent = `Your account was created without a passkey: ${error.message}`;
        return;
      }
    }
    showVerificationPending(result.account.email);
  } catch (error) {
    elements.registerMessage.textContent = error.message;
  } finally {
    setBusy(elements.registerForm, false);
  }
});

elements.resendVerification.addEventListener("click", async () => {
  elements.resendVerification.disabled = true;
  elements.verificationMessage.textContent = "";
  try {
    await api("/api/account/resend-verification", { method: "POST" });
    elements.verificationMessage.textContent = "A new confirmation email was sent.";
  } catch (error) {
    elements.verificationMessage.textContent = error.message;
  } finally {
    elements.resendVerification.disabled = false;
  }
});

elements.forgotPasswordButton.addEventListener("click", showPasswordResetRequest);
document.querySelectorAll("[data-back-to-login]").forEach((button) => button.addEventListener("click", returnToLogin));

elements.passwordResetRequestForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = String(elements.passwordResetRequestForm.elements.namedItem("email")?.value || "").trim();
  setBusy(elements.passwordResetRequestForm, true);
  elements.passwordResetRequestMessage.textContent = "";
  try {
    await api("/api/account/password-reset/request", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
    elements.passwordResetRequestMessage.textContent = "If that verified account exists, a reset link is on its way.";
  } catch (error) {
    elements.passwordResetRequestMessage.textContent = error.message;
  } finally {
    setBusy(elements.passwordResetRequestForm, false);
  }
});

elements.passwordResetForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(elements.passwordResetForm);
  const password = String(values.get("password") || "");
  elements.passwordResetMessage.textContent = "";
  if (password !== values.get("password_confirmation")) {
    elements.passwordResetMessage.textContent = "The passwords do not match.";
    return;
  }
  setBusy(elements.passwordResetForm, true);
  try {
    const token = new URL(window.location.href).searchParams.get("password_reset") || "";
    await api("/api/account/password-reset/complete", {
      method: "POST",
      body: JSON.stringify({ token, password }),
    });
    window.history.replaceState(null, "", "/");
    returnToLogin();
    elements.loginMessage.textContent = "Password changed. Sign in with your new password.";
  } catch (error) {
    elements.passwordResetMessage.textContent = error.message;
  } finally {
    setBusy(elements.passwordResetForm, false);
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
    const result = await api("/api/account/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    if (result.email_verification_required) showVerificationPending(result.email);
    else await loadLibrary();
  } catch (error) {
    elements.loginMessage.textContent = error.message;
  } finally {
    setBusy(elements.loginForm, false);
  }
});

elements.passkeyLoginButton.addEventListener("click", async () => {
  conditionalPasskeyController?.abort();
  conditionalPasskeyController = null;
  elements.loginMessage.textContent = "";
  if (!window.PublicKeyCredential) {
    elements.loginMessage.textContent = "Passkeys are not supported by this browser.";
    return;
  }
  setBusy(elements.loginForm, true);
  try {
    await signInWithPasskey();
  } catch (error) {
    elements.loginMessage.textContent = error.message;
  } finally {
    setBusy(elements.loginForm, false);
  }
});

elements.addPasskeyButton.addEventListener("click", async () => {
  elements.passkeyMessage.textContent = "";
  if (!window.PublicKeyCredential) {
    elements.passkeyMessage.textContent = "Passkeys are not supported by this browser.";
    return;
  }
  elements.addPasskeyButton.disabled = true;
  try {
    await createPasskey();
    await loadPasskeySettings();
  } catch (error) {
    elements.passkeyMessage.textContent = error.message;
  } finally {
    elements.addPasskeyButton.disabled = false;
  }
});

async function loadPasskeySettings() {
  const payload = await api("/api/account/passkeys");
  const count = payload.passkeys?.length || 0;
  elements.passkeyStatus.textContent = count
    ? `${count} passkey${count === 1 ? "" : "s"} registered.`
    : "No passkey registered yet.";
  elements.addPasskeyButton.textContent = count ? "Add another passkey" : "Add a passkey";
}

elements.settingsButton.addEventListener("click", async () => {
  elements.passkeyMessage.textContent = "";
  elements.settingsDialog.showModal();
  try { await loadPasskeySettings(); } catch (error) { elements.passkeyMessage.textContent = error.message; }
});
elements.closeSettings.addEventListener("click", () => elements.settingsDialog.close());
elements.settingsDialog.addEventListener("click", (event) => {
  if (event.target === elements.settingsDialog) elements.settingsDialog.close();
});
elements.plusStatusButton.addEventListener("click", () => elements.plusDialog.showModal());
elements.closePlusDialog.addEventListener("click", () => elements.plusDialog.close());
elements.plusDialog.addEventListener("click", (event) => {
  if (event.target === elements.plusDialog) elements.plusDialog.close();
});
elements.plusDeveloperToggle.addEventListener("change", async () => {
  elements.plusDeveloperToggle.disabled = true;
  try {
    const payload = await api("/api/account/plus-developer-toggle", {
      method: "PUT",
      body: JSON.stringify({ active: elements.plusDeveloperToggle.checked }),
    });
    if (libraryPayload) libraryPayload.plus = payload.plus;
    renderPlusStatus(payload.plus);
  } catch (error) {
    elements.plusDeveloperToggle.checked = !elements.plusDeveloperToggle.checked;
    elements.passkeyMessage.textContent = error.message;
  } finally {
    elements.plusDeveloperToggle.disabled = false;
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

function initializeFeatureStory() {
  const revealItems = [...document.querySelectorAll(".reveal-item")];
  const chapters = [...document.querySelectorAll(".feature-chapter")];
  const prompt = $("#typedExportPrompt");
  const problemChapter = $(".feature-problem");
  const importChapter = $(".feature-import");
  const syncChapter = $(".feature-sync");
  const exportChapter = $(".feature-export");
  const profileChapter = $(".feature-profile");
  const metricsChapter = $(".feature-metrics");
  const networkChapter = $(".feature-network");
  const networkCount = $("#networkCount");
  const networkUnit = $("#networkUnit");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const promptText = prompt?.dataset.text || "";
  let scrollFrame = null;

  chapters.forEach((chapter, index) => {
    if (!chapter.id) chapter.id = `feature-${index + 1}`;
  });
  chapters.forEach((chapter, index) => {
    const next = chapters[index + 1] || $("#story-finale");
    const link = document.createElement("a");
    link.className = "chapter-next";
    link.href = `#${next.id}`;
    link.setAttribute("aria-label", `Continue to ${next.querySelector("h3, h2")?.textContent || "the next section"}`);
    link.textContent = "↓";
    chapter.append(link);
  });

  function scrollProgress(element) {
    if (!element) return 0;
    const bounds = element.getBoundingClientRect();
    const distance = Math.max(1, bounds.height - window.innerHeight);
    return Math.max(0, Math.min(1, (14 - bounds.top) / distance));
  }

  function clampProgress(value) {
    return Math.max(0, Math.min(1, value));
  }

  function stagedProgress(progress, start, duration = .24) {
    return clampProgress((progress - start) / duration);
  }

  function renderScrollSequences() {
    scrollFrame = null;
    if (reducedMotion) return;
    const problemProgress = scrollProgress(problemChapter);
    const problemVisual = problemChapter?.querySelector(".request-visual");
    const visualWidth = problemVisual?.clientWidth || 620;
    const requestTargets = [
      [-.34, .08], [.06, .12], [-.14, .22], [.26, .25], [-.37, .34],
      [.17, .38], [-.03, .46], [.32, .5], [-.24, .56], [.09, .61],
    ];
    problemChapter?.querySelectorAll("[data-request-index]").forEach((item) => {
      const index = Number(item.dataset.requestIndex);
      const localProgress = stagedProgress(problemProgress, .02 + (index * .06), .13);
      const resolveProgress = stagedProgress(problemProgress, .68 + (index * .025), .14);
      const easedDrop = 1 - ((1 - localProgress) ** 3);
      const [xRatio, yRatio] = requestTargets[index];
      const xLimit = Math.max(0, (visualWidth - item.offsetWidth) / 2 - 10);
      const x = Math.max(-xLimit, Math.min(xLimit, xRatio * visualWidth));
      const y = -190 + (easedDrop * (190 + (yRatio * 500)));
      item.style.opacity = String(localProgress * (1 - resolveProgress));
      item.style.filter = `blur(${resolveProgress * 8}px)`;
      item.style.transform = `translate(calc(-50% + ${x}px), ${y - (resolveProgress * 70)}px) rotate(${(index % 2 ? 1 : -1) * (2 + (index % 3))}deg) scale(${1 - (resolveProgress * .35)})`;
    });
    const boostProgress = stagedProgress(problemProgress, .64, .18);
    problemChapter?.querySelectorAll(".sweat-drop").forEach((drop, index) => {
      const localProgress = stagedProgress(problemProgress, .18 + (index * .1), .22);
      drop.style.opacity = String(Math.sin(localProgress * Math.PI) * (1 - boostProgress));
      drop.style.transform = `translateY(${localProgress * 22}px) rotate(25deg)`;
    });
    problemChapter?.querySelectorAll(".typing-spark").forEach((spark, index) => {
      spark.style.opacity = String(.25 + (.75 * Math.abs(Math.sin((problemProgress * (45 + (boostProgress * 65))) + index))));
    });
    problemChapter?.querySelectorAll(".printed-cv").forEach((paper, index) => {
      const localProgress = stagedProgress(problemProgress, .12 + (index * .18), .2);
      const resolveProgress = stagedProgress(problemProgress, .75 + (index * .035), .14);
      paper.style.opacity = String(localProgress * (1 - resolveProgress));
      paper.style.filter = `blur(${resolveProgress * 7}px)`;
      paper.style.transform = `translate(${-95 - (index * 58)}px, ${(localProgress * 76) - (resolveProgress * 55)}px) rotate(${(index - 1) * 5}deg) scale(${1 - (resolveProgress * .3)})`;
    });
    problemChapter?.querySelectorAll(".paper-avalanche i").forEach((paper, index) => {
      const localProgress = stagedProgress(problemProgress, .52 + (index * .025), .16);
      const resolveProgress = stagedProgress(problemProgress, .7 + (index * .025), .16);
      paper.style.opacity = String(localProgress * (1 - resolveProgress));
      paper.style.bottom = `${-105 + (localProgress * (70 + ((index % 3) * 22)))}px`;
      paper.style.filter = `blur(${resolveProgress * 8}px)`;
      const rotation = [-9, 7, -4, 8, -7, 5][index] || 0;
      paper.style.transform = `translateY(${-resolveProgress * 60}px) rotate(${rotation}deg) scale(${1 - (resolveProgress * .35)})`;
    });
    const researcher = problemChapter?.querySelector(".researcher-vector");
    if (researcher) {
      researcher.style.filter = `drop-shadow(0 0 ${boostProgress * 24}px rgba(43,184,139,${boostProgress * .7}))`;
      researcher.style.transform = `translateY(${Math.sin(problemProgress * (70 + boostProgress * 80)) * Math.min(problemProgress, .7) * 1.5}px) scale(${1 + (boostProgress * .025)})`;
    }
    const bottle = problemChapter?.querySelector(".vitamine-bottle");
    const drinkProgress = stagedProgress(problemProgress, .55, .14);
    if (bottle) {
      bottle.style.opacity = String(Math.sin(Math.min(1, drinkProgress) * Math.PI) || (drinkProgress < 1 ? drinkProgress : 0));
      bottle.style.transform = `translate(${-drinkProgress * visualWidth * .36}px, ${-drinkProgress * 82}px) rotate(${-8 - (drinkProgress * 58)}deg) scale(${1 - (drinkProgress * .18)})`;
    }
    problemChapter?.querySelectorAll(".vitamine-energy i").forEach((ring, index) => {
      const ringProgress = stagedProgress(problemProgress, .63 + (index * .035), .18);
      ring.style.opacity = String(Math.sin(ringProgress * Math.PI) * .8);
      ring.style.transform = `scale(${.65 + (ringProgress * .55)})`;
    });
    const boostMessage = problemChapter?.querySelector(".boost-message");
    if (boostMessage) {
      const messageProgress = stagedProgress(problemProgress, .88, .1);
      boostMessage.style.opacity = String(messageProgress);
      boostMessage.style.transform = `translateY(${(1 - messageProgress) * 18}px)`;
    }

    const importProgress = scrollProgress(importChapter);
    const importDocument = importChapter?.querySelector(".import-document");
    if (importDocument) {
      const dropProgress = stagedProgress(importProgress, 0, .38);
      const dissolveProgress = stagedProgress(importProgress, .42, .2);
      importDocument.style.opacity = String(Math.min(dropProgress * 2, 1) * (1 - dissolveProgress));
      importDocument.style.transform = `translate(${dropProgress * 26}px, ${-150 + (dropProgress * 235)}px) scale(${1 - (.16 * dissolveProgress)})`;
    }
    importChapter?.querySelectorAll(".extract-stream i").forEach((dot, index) => {
      const localProgress = stagedProgress(importProgress, .36 + (index * .055), .34);
      dot.style.opacity = String(Math.sin(localProgress * Math.PI));
      dot.style.transform = `translateX(${localProgress * 170}px) scale(${.7 + (.3 * localProgress)})`;
    });
    importChapter?.querySelectorAll(".data-chip").forEach((chip, index) => {
      const localProgress = stagedProgress(importProgress, .5 + (index * .035), .22);
      chip.style.opacity = String(localProgress);
      chip.style.transform = `translateY(${(1 - localProgress) * 26}px) scale(${.9 + (.1 * localProgress)})`;
    });
    const database = importChapter?.querySelector(".database-cylinder");
    if (database) {
      const databaseProgress = stagedProgress(importProgress, .48, .3);
      database.style.transform = `scale(${.82 + (.18 * databaseProgress)})`;
      database.style.boxShadow = `inset 0 0 60px rgba(130,212,184,.07), 0 0 ${20 + (databaseProgress * 70)}px rgba(68,171,137,.24)`;
    }

    const syncProgress = scrollProgress(syncChapter);
    const syncStarts = [0, .13, .06, .21];
    syncChapter?.querySelectorAll(".sync-lines [data-sync-source]").forEach((line) => {
      const source = Number(line.dataset.syncSource);
      const localProgress = stagedProgress(syncProgress, syncStarts[source - 1], .42);
      line.style.strokeDashoffset = String(1 - localProgress);
      line.style.strokeWidth = String(2.5 + (Math.sin(localProgress * Math.PI * 5) * .65));
    });
    syncChapter?.querySelectorAll("[data-sync-pulse]").forEach((pulse) => {
      const source = Number(pulse.dataset.syncPulse);
      const path = syncChapter.querySelector(`#syncPath${source}`);
      const localProgress = stagedProgress(syncProgress, syncStarts[source - 1] + .05, .4);
      if (!path || localProgress <= 0 || localProgress >= 1) {
        pulse.style.opacity = "0";
        return;
      }
      const point = path.getPointAtLength(path.getTotalLength() * localProgress);
      pulse.setAttribute("cx", String(point.x));
      pulse.setAttribute("cy", String(point.y));
      pulse.style.opacity = String(Math.min(1, Math.sin(localProgress * Math.PI) * 1.8));
      pulse.setAttribute("r", String(5 + (Math.sin(localProgress * Math.PI * 4) * 1.2)));
    });
    syncChapter?.querySelectorAll(".cv-document-icon i").forEach((line, index) => {
      const localProgress = stagedProgress(syncProgress, .18 + (index * .16), .22);
      line.style.transform = `scaleX(${localProgress})`;
    });
    syncChapter?.querySelectorAll(".sync-node").forEach((node, index) => {
      const pulse = stagedProgress(syncProgress, index * .18, .18);
      node.style.transform = `scale(${1 + (.1 * Math.sin(pulse * Math.PI))})`;
    });

    const promptProgress = scrollProgress(exportChapter);
    if (prompt) prompt.textContent = promptText.slice(0, Math.floor(promptText.length * promptProgress));

    const profileProgress = scrollProgress(profileChapter);
    const sourceDocument = profileChapter?.querySelector(".profile-source-document");
    if (sourceDocument) {
      const sourceProgress = stagedProgress(profileProgress, 0, .38);
      sourceDocument.style.opacity = String(1 - stagedProgress(profileProgress, .62, .22));
      sourceDocument.style.transform = `translateX(${sourceProgress * 46}px) scale(${1 - (.12 * sourceProgress)})`;
    }
    profileChapter?.querySelectorAll(".profile-transfer i").forEach((dot, index) => {
      const localProgress = stagedProgress(profileProgress, .2 + (index * .08), .38);
      dot.style.opacity = String(Math.sin(localProgress * Math.PI));
      dot.style.transform = `translateX(${localProgress * 125}px)`;
    });
    profileChapter?.querySelectorAll("[data-profile-row]").forEach((row) => {
      const stage = Number(row.dataset.profileRow);
      const localProgress = stagedProgress(profileProgress, .34 + ((stage - 1) * .13), .2);
      row.style.opacity = String(localProgress);
      row.style.transform = `translateY(${(1 - localProgress) * 8}px)`;
    });

    const metricsProgress = scrollProgress(metricsChapter);
    const chartLine = metricsChapter?.querySelector(".chart-line");
    const chartArea = metricsChapter?.querySelector(".chart-area");
    if (chartLine) chartLine.style.strokeDashoffset = String(1 - metricsProgress);
    if (chartArea) chartArea.style.opacity = String(stagedProgress(metricsProgress, .45, .45) * .75);
    const citationTicker = $("#citationTicker");
    const publicationTicker = $("#publicationTicker");
    if (citationTicker) citationTicker.textContent = Math.round(4218 * metricsProgress).toLocaleString();
    if (publicationTicker) publicationTicker.textContent = String(Math.round(82 * metricsProgress));

    const networkProgress = scrollProgress(networkChapter);
    document.querySelectorAll(".map-routes [data-network-stage]").forEach((route) => {
      const stage = Number(route.dataset.networkStage);
      const localProgress = Math.max(0, Math.min(1, (networkProgress - ((stage - 1) * .2)) / .24));
      route.style.strokeDashoffset = String(1 - localProgress);
    });
    document.querySelectorAll(".map-points [data-network-stage]").forEach((point) => {
      const stage = Number(point.dataset.networkStage);
      if (stage === 0) return;
      const localProgress = Math.max(0, Math.min(1, (networkProgress - ((stage - 1) * .2)) / .24));
      point.style.opacity = String(localProgress);
      point.style.transform = `scale(${.65 + (.35 * localProgress)})`;
    });
    const collaboratorCount = 1 + Math.round(networkProgress * 17);
    if (networkCount) networkCount.textContent = String(collaboratorCount);
    if (networkUnit) networkUnit.textContent = collaboratorCount === 1 ? "collaborator" : "collaborators";
  }

  function requestSequenceRender() {
    if (scrollFrame !== null) return;
    scrollFrame = window.requestAnimationFrame(renderScrollSequences);
  }

  if (!("IntersectionObserver" in window) || reducedMotion) {
    revealItems.forEach((item) => item.classList.add("is-visible"));
    if (prompt) prompt.textContent = promptText;
    document.querySelectorAll("[data-scroll-stage], .data-chip, [data-profile-row]").forEach((item) => { item.style.opacity = "1"; item.style.transform = "none"; });
    document.querySelectorAll("[data-request-index], .sweat-drop, .paper-avalanche i").forEach((item) => { item.style.opacity = "0"; });
    const boostMessage = $(".boost-message");
    if (boostMessage) boostMessage.style.opacity = "1";
    const importDocument = $(".import-document");
    if (importDocument) { importDocument.style.opacity = "1"; importDocument.style.transform = "none"; }
    document.querySelectorAll(".sync-lines path, .chart-line").forEach((line) => { line.style.strokeDashoffset = "0"; });
    document.querySelectorAll(".cv-document-icon i").forEach((line) => { line.style.transform = "scaleX(1)"; });
    const chartArea = $(".chart-area");
    if (chartArea) chartArea.style.opacity = ".75";
    if ($("#citationTicker")) $("#citationTicker").textContent = "4,218";
    if ($("#publicationTicker")) $("#publicationTicker").textContent = "82";
    document.querySelectorAll(".map-routes [data-network-stage]").forEach((route) => { route.style.strokeDashoffset = "0"; });
    document.querySelectorAll(".map-points [data-network-stage]").forEach((point) => { point.style.opacity = "1"; });
    if (networkCount) networkCount.textContent = "18";
    if (networkUnit) networkUnit.textContent = "collaborators";
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    }
  }, { rootMargin: "0px 0px -12%", threshold: 0.16 });
  revealItems.forEach((item) => observer.observe(item));
  window.addEventListener("scroll", requestSequenceRender, { passive: true });
  window.addEventListener("resize", requestSequenceRender);
  renderScrollSequences();

  $("#returnToTop")?.addEventListener("click", (event) => {
    event.preventDefault();
    const root = document.documentElement;
    root.classList.add("returning-home");
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    const returnHome = () => {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      root.scrollTop = 0;
      document.body.scrollTop = 0;
    };
    returnHome();
    window.requestAnimationFrame(() => {
      returnHome();
      window.requestAnimationFrame(returnHome);
    });
    window.setTimeout(() => {
      returnHome();
      root.classList.remove("returning-home");
    }, 450);
  });
}

initializeFeatureStory();

(async function initialize() {
  showAuth();
  const initialUrl = new URL(window.location.href);
  const confirmation = initialUrl.searchParams.get("email_confirmation");
  if (initialUrl.searchParams.get("password_reset")) {
    elements.authChoice.hidden = true;
    elements.registerForm.hidden = true;
    elements.verificationPending.hidden = true;
    elements.passwordResetRequestForm.hidden = true;
    elements.passwordResetForm.hidden = false;
    elements.passwordResetForm.elements.namedItem("password").focus();
    return;
  }
  try {
    const session = await api("/api/session");
    if (session.account) {
      if (session.email_verified) {
        await loadLibrary();
        if (confirmation === "verified") elements.libraryMessage.textContent = "Email address confirmed.";
      } else {
        showVerificationPending(session.email);
      }
    } else {
      showRegistration();
    }
  } catch {
    showAuthTab("login");
    if (confirmation === "invalid") elements.loginMessage.textContent = "That confirmation link is invalid or expired.";
    if (window.PublicKeyCredential?.isConditionalMediationAvailable) {
      try {
        if (await PublicKeyCredential.isConditionalMediationAvailable()) {
          conditionalPasskeyController = new AbortController();
          await signInWithPasskey({ conditional: true });
        }
      } catch (error) {
        if (error.name !== "AbortError" && error.name !== "NotAllowedError") console.debug("Conditional passkey sign-in unavailable", error);
      }
    }
  }
})();
