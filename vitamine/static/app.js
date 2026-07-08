const state = {
  sections: {},
  entries: [],
  publications: [],
  journalMetrics: [],
  identifiers: [],
  connections: {},
  cvImport: {},
  zoteroCollections: [],
  zoteroLibraries: [],
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
  selectedPublicationId: null,
  suppressPublicationClick: false,
  publicationSort: { key: "year", direction: "desc" },
  draggedPublicationId: null,
  draggedDropProfile: null,
  draggedDropId: null,
  draggedBiosketchContributionId: null,
  draggedBiosketchPublicationId: null,
  selectedBiosketchContributionId: null,
  collaborationMap: {
    data: null,
    zoom: 2,
    origin: null,
    drag: null,
  },
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function on(selector, eventName, handler, options) {
  const element = $(selector);
  if (element) element.addEventListener(eventName, handler, options);
  return element;
}

function setStatus(text) {
  $("#status").textContent = text;
}

async function api(path, options = {}) {
  const headers = options.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const response = await fetch(path, {
    headers,
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
    "#syncSourcesDashboard",
    "#enrichDoiDashboard",
    "#maintainPubsDashboard",
    "#fetchJournalMetrics",
    "#saveJournalMetrics",
    "#connectionsForm button[type='submit']",
    "#connectZotero",
    "#testZoteroConnection",
    "#loadZoteroCollections",
    "#useExampleDatabase",
    "#createBlankDatabase",
    "#loadDatabase",
    "#saveCvImportSettings",
    "#chooseCvImportFile",
    "#importCvFile",
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
  if (!disabled && $("#connectionZoteroSource")) updateZoteroSourceVisibility();
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
  $("#cvImportProvider").value = data.provider || "none";
  $("#cvImportOllamaUrl").value = data.ollama_url || "http://127.0.0.1:11434";
  $("#cvImportOllamaModel").value = data.ollama_model || "llama3.1:8b";
  $("#cvImportApiBaseUrl").value = data.api_base_url || "https://api.openai.com/v1";
  $("#cvImportApiModel").value = data.api_model || "gpt-4.1-mini";
  $("#cvImportApiKey").value = "";
  $("#cvImportKeyStatus").textContent = data.api_key_set ? "API key saved" : "No API key";
  updateCvImportProviderVisibility();
}

function updateCvImportProviderVisibility() {
  const providerSelect = $("#cvImportProvider");
  if (!providerSelect) return;
  const provider = providerSelect.value;
  const ollamaFields = $("#cvImportOllamaFields");
  const apiFields = $("#cvImportApiFields");
  const apiBaseUrlField = $("#cvImportApiBaseUrlField");
  const apiModelField = $("#cvImportApiModelField");
  if (ollamaFields) ollamaFields.hidden = provider !== "ollama";
  if (apiFields) apiFields.hidden = !["openai", "openai_compatible"].includes(provider);
  if (apiBaseUrlField) apiBaseUrlField.hidden = provider !== "openai_compatible";
  if (apiModelField) apiModelField.hidden = provider !== "openai_compatible";
}

async function saveCvImportSettings() {
  if (!$("#cvImportProvider")) return;
  await api("/api/cv-import/settings", {
    method: "PUT",
    body: JSON.stringify({
      provider: $("#cvImportProvider").value,
      ollama_url: $("#cvImportOllamaUrl").value,
      ollama_model: $("#cvImportOllamaModel").value,
      api_base_url: $("#cvImportApiBaseUrl").value,
      api_model: $("#cvImportApiModel").value,
      api_key: $("#cvImportApiKey").value,
    }),
  });
  setStatus("CV import settings saved");
  await loadCvImportSettings();
}

async function importCvFiles(files) {
  files = Array.from(files || []);
  if (!files.length) return;
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  setStatus(`Importing ${files.length} CV document${files.length === 1 ? "" : "s"}...`);
  setActionButtons(true);
  try {
    const data = await api("/api/cv-import/upload", {
      method: "POST",
      body: form,
    });
    const mode = data.used_llm ? "LLM" : "heuristic";
    const warningText = (data.warnings || []).length ? `; ${data.warnings.length} warning(s)` : "";
    if ($("#cvImportOutput")) $("#cvImportOutput").textContent = JSON.stringify(data, null, 2);
    setStatus(`Imported ${data.entries_inserted || 0} entries and ${data.contributions_inserted || 0} contributions via ${mode}${warningText}`);
    await loadSummary();
    await loadMetrics();
    await loadEntries();
    await loadPerson();
    await loadBiosketch();
  } catch (error) {
    if ($("#cvImportOutput")) $("#cvImportOutput").textContent = error.message;
    setStatus(error.message);
  } finally {
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
  $("#publicationSourcePolicy").value = data.publication_source_policy || "zotero_primary_orcid_validation";
  renderZoteroCollections();
  $("#connectionStatus").textContent = data.zotero_api_key_set ? "Zotero key saved" : "No Zotero key";
  updateZoteroSourceVisibility();
}

async function saveConnections(event) {
  event.preventDefault();
  const sourceMode = $("#connectionZoteroSource").value;
  const selected = sourceMode === "collection" ? selectedZoteroCollection() : null;
  await api("/api/connections", {
    method: "PUT",
    body: JSON.stringify({
      orcid_id: $("#connectionOrcid").value,
      zotero_api_key: $("#connectionZoteroKey").value,
      zotero_library_value: $("#connectionZoteroLibrary").value,
      zotero_source_mode: sourceMode,
      zotero_collection_key: selected?.key || "",
      zotero_collection_name: selected?.name || "",
      publication_source_policy: $("#publicationSourcePolicy").value,
    }),
  });
  $("#connectionZoteroKey").value = "";
  setStatus("Connections saved");
  await loadConnections();
  if (state.connections.zotero_api_key_set) {
    await testZoteroConnection();
  }
  await loadPersonIdentifiers();
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
  const data = await api("/api/zotero/connect-url");
  window.open(data.url, "_blank", "noopener,width=980,height=760,left=0,top=0");
  setStatus(data.oauth_available ? "Opening Zotero authorization" : "Opening Zotero key setup");
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
    metricCard("Visible pubs", pubs.visible || 0),
    metricCard("Peer reviewed", pubs.peer_reviewed || 0),
    metricCard("Short selected", pubs.selected_short || 0),
    metricCard("Ultrashort", pubs.selected_ultrashort || 0),
    metricCard("ORCID matched", pubs.orcid_matched || 0),
    metricCard("Citation metrics", pubs.citation_metric_count || 0),
    metricCard("OpenAlex Citations", pubs.openalex_cited_by_total || 0),
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
  const data = await api("/api/collaboration-map");
  state.collaborationMap.data = data;
  if (!state.collaborationMap.origin) {
    state.collaborationMap.zoom = MAP_ZOOM;
    state.collaborationMap.origin = defaultMapOrigin(MAP_ZOOM);
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
    container.innerHTML = `
      <div class="emptyMap">
        <strong>No collaboration geography yet</strong>
        <span>Run DOI metadata enrichment to collect OpenAlex institution locations.</span>
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
      const radius = Math.min(12, 3.5 + Math.sqrt(Number(node.publication_count || 1)) * 2);
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
    `<span>Links: <strong>${data.publication_links}</strong></span>`,
  ].join("");
  $("#collaborationCountries").innerHTML = (data.top_countries || [])
    .map((row) => `<span>${escapeHtml(row.country)}: <strong>${row.publication_count}</strong></span>`)
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
  const oldOrigin = state.collaborationMap.origin || defaultMapOrigin(oldZoom);
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
  state.collaborationMap.origin = defaultMapOrigin(MAP_ZOOM);
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
      origin: { ...(state.collaborationMap.origin || defaultMapOrigin(state.collaborationMap.zoom || MAP_ZOOM)) },
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

function exportLanguage() {
  return $("#exportLanguage")?.value || "en";
}

async function loadExportSettings() {
  const data = await api("/api/export-settings");
  const label = data.home_language_label || "Deutsch";
  $("#homeLanguageLabel").value = label;
  $("#homeLanguageOption").textContent = label;
}

async function saveExportSettings() {
  const label = $("#homeLanguageLabel").value.trim() || "Deutsch";
  $("#homeLanguageLabel").value = label;
  $("#homeLanguageOption").textContent = label;
  await api("/api/export-settings", {
    method: "PUT",
    body: JSON.stringify({ home_language_label: label }),
  });
  setStatus("Native / second CV language saved");
}

function refreshExportLinks(profile, data) {
  const selectors = {
    ultrashort: {
      docx: "#openUltraDashboardDocx",
      html: "#openUltraDashboardHtml",
      pdf: "#openUltraDashboardPdf",
    },
    short: {
      docx: "#openShortDashboardDocx",
      html: "#openShortDashboardHtml",
      pdf: "#openShortDashboardPdf",
    },
    long: {
      docx: "#openLongDashboardDocx",
      html: "#openLongDashboardHtml",
      pdf: "#openLongDashboardPdf",
    },
    biosketch: {
      docx: "#openBiosketchDashboardDocx",
      html: "#openBiosketchDashboardHtml",
      pdf: "#openBiosketchDashboardPdf",
    },
  }[profile];
  if (!selectors) return;
  Object.entries(selectors).forEach(([format, selector]) => {
    if (data[format]) enableExportLink(selector, data[format], data[`${format}_path`]);
  });
}

function enableExportLink(selector, href, path = "") {
  const link = $(selector);
  if (!link) return;
  link.href = href;
  if (path) link.title = path;
  link.classList.remove("disabledLink");
  link.removeAttribute("aria-disabled");
  link.removeAttribute("tabindex");
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
  await loadConnections();
}

async function deleteIdentifier() {
  const id = $("#identifierId").value;
  if (!id) return;
  await api(`/api/person/identifiers/${id}`, { method: "DELETE" });
  setStatus("Identifier deleted");
  clearIdentifierForm();
  await loadPersonIdentifiers();
  await loadConnections();
}

async function savePerson(event) {
  event.preventDefault();
  const payload = {};
  new FormData($("#personForm")).forEach((value, key) => {
    payload[key] = value;
  });
  await api("/api/person", { method: "PUT", body: JSON.stringify(payload) });
  await saveExportSettings();
  setStatus("Person saved");
  await loadCollaborationMap();
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
  $("#publicationCategory").value = "peer_reviewed";
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

function editPublication(id) {
  const pub = state.publications.find((row) => row.id === id);
  if (!pub) return;
  state.selectedPublicationId = id;
  $("#publicationId").value = pub.id;
  $("#publicationTitle").value = pub.title || "";
  $("#publicationAuthors").value = pub.authors || "";
  $("#publicationYear").value = pub.year || "";
  $("#publicationCategory").value = pub.category || "";
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
  $$("#publicationsBody tr[data-publication-id]").forEach((row) => {
    row.classList.toggle("selected", Number(row.dataset.publicationId) === id);
  });
  openPublicationEditor();
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
  const data = await api(path, { method, body: JSON.stringify(payload) });
  state.selectedPublicationId = Number(id || data.id || 0) || null;
  if (state.selectedPublicationId) {
    await syncPublicationProfileFlag("ultrashort", state.selectedPublicationId, payload.include_ultrashort);
    await syncPublicationProfileFlag("short", state.selectedPublicationId, payload.include_short);
  }
  closePublicationEditor();
  setStatus("Publication saved");
  await loadPublications();
  await loadSummary();
  await loadExportProfile("ultrashort");
  await loadExportProfile("short");
  await loadBiosketch();
}

async function deletePublication() {
  const id = $("#publicationId").value;
  if (!id) return;
  await api(`/api/publications/${id}`, { method: "DELETE" });
  clearPublicationForm();
  closePublicationEditor();
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
    await loadCollaborationMap();
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
  $("#connectionsForm").addEventListener("submit", saveConnections);
  $("#connectZotero").addEventListener("click", connectZotero);
  $("#testZoteroConnection").addEventListener("click", testZoteroConnection);
  $("#loadZoteroCollections").addEventListener("click", loadZoteroCollections);
  $("#connectionZoteroSource").addEventListener("change", updateZoteroSourceVisibility);
  $("#useExampleDatabase").addEventListener("click", useExampleDatabase);
  $("#createBlankDatabase").addEventListener("click", createBlankDatabase);
  $("#loadDatabase").addEventListener("click", chooseDatabaseFile);
  on("#cvImportProvider", "change", updateCvImportProviderVisibility);
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
  $("#newBiosketchAchievement").addEventListener("click", createBiosketchAchievement);
  $("#deleteBiosketchAchievement").addEventListener("click", deleteBiosketchAchievement);
  $$("#publicationsView th[data-sort]").forEach((header) => {
    header.addEventListener("click", () => sortPublicationsBy(header.dataset.sort));
  });
  $("#newPublication").addEventListener("click", () => {
    clearPublicationForm();
    openPublicationEditor();
  });
  $("#closePublicationEditor").addEventListener("click", closePublicationEditor);
  $("#publicationDialog").addEventListener("click", (event) => {
    if (event.target === $("#publicationDialog")) closePublicationEditor();
  });
  $("#publicationForm").addEventListener("submit", savePublication);
  $("#deletePublication").addEventListener("click", deletePublication);
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
  const syncSources = async () => {
    $("#syncOutput").textContent = "Syncing publication sources...";
    const data = await runAction("/api/actions/sync-publication-sources", "Publication sources synced", "Syncing publication sources...");
    actionLog("#syncOutput", data);
  };
  const buildUltra = async () => {
    const language = exportLanguage();
    $("#exportOutput").textContent = `Building tabular one page CV (${language})...`;
    const data = await runAction(`/api/actions/build-ultrashort-tabular?lang=${encodeURIComponent(language)}`, "Tabular one page CV built", "Building tabular one page CV...");
    refreshExportLinks("ultrashort", data);
    actionLog("#exportOutput", data);
    openBuiltArtifact(data);
  };
  const buildLong = async () => {
    const language = exportLanguage();
    $("#exportOutput").textContent = `Building long CV (${language})...`;
    const data = await runAction(`/api/actions/build-long?lang=${encodeURIComponent(language)}`, "Long CV built", "Building long CV...");
    refreshExportLinks("long", data);
    actionLog("#exportOutput", data);
    openBuiltArtifact(data);
  };
  const buildShort = async () => {
    const language = exportLanguage();
    $("#exportOutput").textContent = `Building short CV (${language})...`;
    const data = await runAction(`/api/actions/build-short?lang=${encodeURIComponent(language)}`, "Short CV built", "Building short CV...");
    refreshExportLinks("short", data);
    actionLog("#exportOutput", data);
    openBuiltArtifact(data);
  };
  const buildBiosketch = async () => {
    const language = exportLanguage();
    $("#exportOutput").textContent = `Building biosketch (${language})...`;
    const data = await runAction(`/api/actions/build-biosketch?lang=${encodeURIComponent(language)}`, "Biosketch built", "Building biosketch...");
    refreshExportLinks("biosketch", data);
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
  $("#syncSourcesDashboard").addEventListener("click", syncSources);
  $("#syncOrcidDashboard").addEventListener("click", syncOrcid);
  $("#enrichDoiDashboard").addEventListener("click", enrichDoi);
  $("#maintainPubsDashboard").addEventListener("click", maintainPubs);
  $("#fetchJournalMetrics").addEventListener("click", fetchJournalMetrics);
  $("#buildUltraDashboard").addEventListener("click", buildUltra);
  $("#buildShortDashboard").addEventListener("click", buildShort);
  $("#buildLongDashboard").addEventListener("click", buildLong);
  $("#buildBiosketchDashboard").addEventListener("click", buildBiosketch);
  $("#homeLanguageLabel").addEventListener("change", saveExportSettings);
  await loadSummary();
  await loadDatabaseInfo();
  await loadCvImportSettings();
  await loadConnections();
  await loadExportSettings();
  await loadMetrics();
  await loadCollaborationMap();
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
