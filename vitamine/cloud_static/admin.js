(() => {
  const $ = (selector) => document.querySelector(selector);
  const number = new Intl.NumberFormat();
  const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  let dashboard = null;

  const text = (value) => String(value ?? "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));
  const money = (microusd) => `$${(Number(microusd || 0) / 1_000_000).toFixed(4)}`;
  const days = (value) => value === null || value === undefined ? "No activity" : value === 0 ? "Today" : `${value}d ago`;

  async function request(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw Object.assign(new Error(data.detail || "Request failed."), { status: response.status });
    }
    return response.json();
  }

  function drawOverview(data) {
    const metrics = [
      ["Accounts", number.format(data.members)],
      ["Active in 7 days", number.format(data.active_7_days)],
      ["LLM calls · 30 days", number.format(data.llm_calls_30_days)],
      ["Underlying API cost · 30 days", money(data.cost_microusd_30_days)],
      ["Unpriced responses · 30 days", number.format(data.unpriced_responses_30_days)],
      ["Managed AI paused", number.format(data.managed_ai_paused_accounts)],
    ];
    $("#overview").innerHTML = metrics.map(([label, value]) => `<article class="metric"><p>${label}</p><strong>${value}</strong></article>`).join("");
  }

  function drawTrend(rows) {
    const max = Math.max(1, ...rows.map((row) => row.logins + row.enrichments + row.llm_calls));
    $("#trend").innerHTML = rows.map((row) => {
      const bars = [["login", row.logins], ["enrich", row.enrichments], ["llm", row.llm_calls]];
      return `<div class="day" title="${row.day}: ${row.logins} logins, ${row.enrichments} enrichment calls, ${row.llm_calls} LLM calls">${bars.map(([kind, count]) => `<span class="bar ${kind}" style="height:${Math.max(2, count / max * 100)}%"></span>`).join("")}</div>`;
    }).join("");
  }

  function drawJobs(rows) {
    $("#jobs").innerHTML = rows.length ? rows.map((row) => `<div class="job"><b>${text(row.kind.replace("_", " "))}</b> · ${text(row.status)}: ${number.format(row.count)}</div>`).join("") : "<p>No background jobs in the last 30 days.</p>";
  }

  function status(member) {
    if (member.unpriced_24h) return `<span class="status-badge critical">Paused · unpriced</span>`;
    if (member.managed_ai_paused) return `<span class="status-badge warning">Paused · $0.20</span>`;
    return `<span class="status-badge">Available</span>`;
  }

  function drawJobAlerts(rows) {
    const labels = { expensive: "High cost", repeated: "Repeated", failed: "Failed", stuck: "Stuck", unpriced: "Unpriced" };
    $("#jobAlerts").innerHTML = rows.map((row) => `<tr><td>${text(row.reference)}</td><td><span class="status-badge ${text(row.signal)}">${text(labels[row.signal] || row.signal)}</span></td><td>${text(row.kind.replace("_", " "))}</td><td>${number.format(row.count)}</td><td>${row.cost_microusd ? money(row.cost_microusd) : "—"}</td><td>${row.age_minutes === null || row.age_minutes === undefined ? "—" : `${number.format(row.age_minutes)}m`}</td></tr>`).join("") || '<tr><td colspan="6">No jobs need operator review.</td></tr>';
  }

  function drawMembers() {
    const query = $("#memberFilter").value.trim().toLowerCase();
    const rows = dashboard.members.filter((member) => `${member.reference} ${days(member.inactive_days)}`.toLowerCase().includes(query));
    $("#members").innerHTML = rows.map((member) => `<tr><td>${text(member.reference)}</td><td>${status(member)}</td><td>${days(member.inactive_days)}</td><td>${number.format(member.account_age_days || 0)}d</td><td>${number.format(member.logins_since_dashboard_enabled)}</td><td>${number.format(member.enrichment_calls)}</td><td>${number.format(member.llm_calls)}</td><td>${compact.format(member.input_tokens)} / ${compact.format(member.output_tokens)}</td><td>${money(member.cost_microusd)}</td></tr>`).join("") || '<tr><td colspan="9">No matching anonymized members.</td></tr>';
  }

  function drawDashboard(data) {
    dashboard = data;
    $("#privacyNotice").textContent = data.privacy;
    $("#generatedAt").textContent = `Updated ${new Date(data.generated_at).toLocaleString()}`;
    drawOverview(data.overview);
    drawTrend(data.daily_30_days);
    drawJobs(data.jobs_30_days);
    drawJobAlerts(data.job_alerts);
    drawMembers();
  }

  async function loadDashboard() {
    try {
      drawDashboard(await request("/api/admin/dashboard"));
      $("#loginPanel").hidden = true;
      $("#dashboard").hidden = false;
    } catch (error) {
      $("#dashboard").hidden = true;
      $("#loginPanel").hidden = false;
      if (error.status === 503) $("#loginMessage").textContent = error.message;
    }
  }

  $("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("#loginMessage").textContent = "";
    try {
      await request("/api/admin/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: $("#username").value, password: $("#password").value }) });
      $("#password").value = "";
      await loadDashboard();
    } catch (error) { $("#loginMessage").textContent = error.message; }
  });
  $("#refreshButton").addEventListener("click", loadDashboard);
  $("#logoutButton").addEventListener("click", async () => { await request("/api/admin/logout", { method: "POST" }); await loadDashboard(); });
  $("#memberFilter").addEventListener("input", drawMembers);
  loadDashboard();
})();
