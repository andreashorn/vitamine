const body = document.body;
const slug = body.dataset.profileSlug;
const embedded = body.dataset.profileEmbedded === "true";
const requestedBlock = body.dataset.profileBlock || "";
const BLOCK_LABELS = {
  bio: "About",
  metrics: "Citations",
  publications: "Publications",
  collaborators: "Collaborators",
};
const BLOCK_KEYS = Object.keys(BLOCK_LABELS);
const PUBLICATION_PAGE_SIZE = 25;
const PROFILE_MAP_WIDTH = 1000;
const PROFILE_MAP_HEIGHT = 520;
const PROFILE_MAP_TILE_SIZE = 256;
const PROFILE_MAP_DEFAULT_ZOOM = 2;
const PROFILE_MAP_MIN_ZOOM = 2;
const PROFILE_MAP_MAX_ZOOM = 6;

const state = {
  profile: null,
  owner: false,
  embedTarget: "",
  layoutDraft: [],
  publications: {
    query: "",
    page: 0,
  },
  collaborationMap: {
    data: null,
    zoom: PROFILE_MAP_DEFAULT_ZOOM,
    origin: null,
    drag: null,
  },
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function safeUrl(value) {
  try {
    const url = new URL(String(value || ""), window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function doiUrl(value) {
  const doi = String(value || "")
    .trim()
    .replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/i, "");
  if (!/^10\.\d{4,9}\/\S+$/i.test(doi)) return "";
  return `https://doi.org/${encodeURIComponent(doi).replaceAll("%2F", "/")}`;
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: options.body
      ? { "Content-Type": "application/json", ...(options.headers || {}) }
      : options.headers,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "VitaMine could not complete that request.");
  return payload;
}

function normalizedBlocks(profile) {
  const seen = new Set();
  const blocks = [];
  for (const item of Array.isArray(profile.blocks) ? profile.blocks : []) {
    const key = String(item?.key || "").toLowerCase();
    if (!BLOCK_KEYS.includes(key) || seen.has(key)) continue;
    blocks.push({ key, visible: item.visible !== false });
    seen.add(key);
  }
  for (const key of BLOCK_KEYS) {
    if (!seen.has(key)) blocks.push({ key, visible: true });
  }
  return blocks;
}

function publicProfileTitle(profile) {
  const bio = profile?.bio || {};
  return profile?.profile_title
    || bio.display_name
    || profile?.display_name
    || "Academic profile";
}

function blockHeader(key, subtitle = "") {
  const embedButton = state.owner && !embedded
    ? `<button class="block-embed-button" type="button" data-embed-block="${key}">Embed block</button>`
    : "";
  return `
    <div class="block-heading">
      <div>
        ${subtitle ? `<p class="profile-eyebrow">${escapeHtml(subtitle)}</p>` : ""}
        <h2>${escapeHtml(BLOCK_LABELS[key])}</h2>
      </div>
      ${embedButton}
    </div>`;
}

function renderHero(profile) {
  const bio = profile.bio || {};
  const hero = $("#profileHero");
  const name = publicProfileTitle(profile);
  const headline = profile.headline
    || [bio.position_title, bio.institution].filter(Boolean).join(" · ");
  hero.innerHTML = `
    <p class="profile-eyebrow">Public academic profile</p>
    <h1>${escapeHtml(name)}</h1>
    <p class="profile-headline">${escapeHtml(headline)}</p>`;
  hero.hidden = embedded && requestedBlock && requestedBlock !== "bio";
}

function renderBio(profile) {
  const bio = profile.bio || {
    display_name: profile.display_name,
    biography: profile.biography,
    orcid: profile.orcid,
  };
  const portraitUrl = safeUrl(profile.portrait_url);
  const portraitWidth = Number(profile.portrait_width) > 0 ? Number(profile.portrait_width) : 4;
  const portraitHeight = Number(profile.portrait_height) > 0 ? Number(profile.portrait_height) : 5;
  const portrait = portraitUrl
    ? `<figure class="profile-portrait-frame">
        <img
          class="profile-portrait"
          src="${escapeHtml(portraitUrl)}"
          alt="Portrait of ${escapeHtml(bio.display_name || publicProfileTitle(profile))}"
          width="${portraitWidth}"
          height="${portraitHeight}"
        >
      </figure>`
    : "";
  const orcid = String(bio.orcid || profile.orcid || "");
  const meta = [
    bio.degrees,
    bio.country,
    orcid ? `<a href="https://orcid.org/${encodeURIComponent(orcid)}" target="_blank" rel="me noreferrer">ORCID ${escapeHtml(orcid)}</a>` : "",
  ].filter(Boolean);
  return `
    <section class="profile-block bio-block" data-profile-block="bio">
      ${blockHeader("bio", "Biography")}
      <div class="bio-content${portrait ? " has-portrait" : ""}">
        ${portrait}
        <div class="bio-copy">
          ${bio.biography ? `<div class="profile-biography">${escapeHtml(bio.biography)}</div>` : '<p class="empty-copy">No public biography has been added yet.</p>'}
          ${meta.length ? `<div class="bio-meta">${meta.map((item) =>
            String(item).startsWith("<a") ? item : `<span>${escapeHtml(item)}</span>`
          ).join("")}</div>` : ""}
        </div>
      </div>
    </section>`;
}

function metricCard(label, value, note) {
  return `
    <article class="impact-stat" title="${escapeHtml(note)}">
      <strong>${formatNumber(value)}</strong>
      <span>${escapeHtml(label)}</span>
    </article>`;
}

function citationChart(metrics) {
  const years = Array.isArray(metrics.by_year) ? metrics.by_year : [];
  const max = Math.max(...years.map((row) => Number(row.citations || 0)), 0);
  if (!years.length || !max) return '<p class="empty-copy">No yearly citation history is available yet.</p>';
  return `
    <div class="public-citation-chart">
      <div class="public-citation-bars" style="--citation-year-count:${years.length}">
        ${years.map((row, index) => {
          const height = Math.max(5, Number(row.citations || 0) / max * 100);
          return `
            <button
              class="public-citation-bar"
              type="button"
              data-citation-index="${index}"
              aria-label="${escapeHtml(row.year)}: ${formatNumber(row.citations)} citations"
            >
              <span>${formatNumber(row.citations)}</span>
              <i><b style="height:${height.toFixed(1)}%"></b></i>
              <strong>${escapeHtml(row.year)}</strong>
            </button>`;
        }).join("")}
      </div>
      <div id="publicCitationDetail" class="public-citation-detail" hidden></div>
    </div>`;
}

function renderMetrics(profile) {
  const metrics = profile.metrics || {};
  const all = metrics.all || {};
  const recent = metrics.recent || {};
  return `
    <section class="profile-block metrics-block" data-profile-block="metrics">
      ${blockHeader("metrics")}
      <div class="impact-stats">
        ${metricCard("Publications", metrics.total_publications, "All visible publications in this public profile.")}
        ${metricCard("Peer reviewed", metrics.peer_reviewed_publications, "Visible publications categorized as peer reviewed.")}
        ${metricCard("Citations", metrics.total_citations, "Cumulative citations received by the publications in this profile.")}
        ${metricCard("h-index", all.h_index, "The largest h for which h publications have at least h citations each.")}
      </div>
      <div class="citation-summary">
        <div class="citation-summary-table">
          <span></span><strong>All</strong><strong>Since ${escapeHtml(metrics.since_year || "")}</strong>
          <span>Citations</span><strong>${formatNumber(all.citations)}</strong><strong>${formatNumber(recent.citations)}</strong>
          <span>h-index</span><strong>${formatNumber(all.h_index)}</strong><strong>${formatNumber(recent.h_index)}</strong>
          <span>i10-index</span><strong>${formatNumber(all.i10_index)}</strong><strong>${formatNumber(recent.i10_index)}</strong>
        </div>
        ${citationChart(metrics)}
      </div>
    </section>`;
}

function publicationCard(publication) {
  const url = safeUrl(publication.url);
  const doi = String(publication.doi || "").trim();
  const doiHref = doiUrl(doi);
  const title = url
    ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(publication.title)}</a>`
    : escapeHtml(publication.title);
  const doiMarkup = doiHref
    ? `<a class="publication-doi" href="${escapeHtml(doiHref)}" target="_blank" rel="noreferrer">DOI ${escapeHtml(doi)}</a>`
    : doi
      ? `<span>DOI ${escapeHtml(doi)}</span>`
      : "";
  return `
    <article class="public-publication">
      <span class="publication-year">${escapeHtml(publication.year || "")}</span>
      <div class="publication-record">
        <h3>${title}</h3>
        ${publication.authors ? `<p class="publication-authors" title="${escapeHtml(publication.authors)}">${escapeHtml(publication.authors)}</p>` : ""}
        <div class="publication-meta">
          ${publication.venue ? `<span>${escapeHtml(publication.venue)}</span>` : ""}
          ${publication.citations ? `<span>${formatNumber(publication.citations)} citations</span>` : ""}
          ${doiMarkup}
        </div>
      </div>
    </article>`;
}

function publicationSearchText(publication) {
  return [
    publication.title,
    publication.authors,
    publication.venue,
    publication.year,
    publication.doi,
  ].join(" ").toLowerCase();
}

function filteredPublications() {
  const publications = Array.isArray(state.profile?.publications) ? state.profile.publications : [];
  const terms = state.publications.query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  if (!terms.length) return publications;
  return publications.filter((publication) => {
    const haystack = publicationSearchText(publication);
    return terms.every((term) => haystack.includes(term));
  });
}

function publicationPage() {
  const publications = filteredPublications();
  const pageCount = Math.max(1, Math.ceil(publications.length / PUBLICATION_PAGE_SIZE));
  state.publications.page = Math.max(0, Math.min(state.publications.page, pageCount - 1));
  const start = state.publications.page * PUBLICATION_PAGE_SIZE;
  return {
    publications,
    pageCount,
    start,
    rows: publications.slice(start, start + PUBLICATION_PAGE_SIZE),
  };
}

function publicationCountLabel(page) {
  if (!page.publications.length) {
    return state.publications.query ? "No matching publications" : "No publications";
  }
  if (page.publications.length <= PUBLICATION_PAGE_SIZE) {
    return `${formatNumber(page.publications.length)} publication${page.publications.length === 1 ? "" : "s"}`;
  }
  const end = Math.min(page.start + PUBLICATION_PAGE_SIZE, page.publications.length);
  return `${formatNumber(page.start + 1)}–${formatNumber(end)} of ${formatNumber(page.publications.length)}`;
}

function publicationResultsMarkup(page) {
  return page.rows.map(publicationCard).join("")
    || `<p class="empty-copy">${state.publications.query
      ? "No publications match this search."
      : "No public publications yet."}</p>`;
}

function publicationPaginationMarkup(page) {
  if (page.pageCount <= 1) return "";
  return `
    <button type="button" data-publication-page="previous" ${state.publications.page === 0 ? "disabled" : ""}>Previous</button>
    <span>Page ${formatNumber(state.publications.page + 1)} of ${formatNumber(page.pageCount)}</span>
    <button type="button" data-publication-page="next" ${state.publications.page >= page.pageCount - 1 ? "disabled" : ""}>Next</button>`;
}

function renderPublications(profile) {
  const publications = Array.isArray(profile.publications) ? profile.publications : [];
  const page = publicationPage();
  return `
    <section class="profile-block publications-block" data-profile-block="publications">
      ${blockHeader("publications", `${formatNumber(publications.length)} public records`)}
      <div class="publication-search">
        <label>
          <span class="visually-hidden">Search publications</span>
          <input id="publicationSearch" type="search" value="${escapeHtml(state.publications.query)}" placeholder="Search title, author, journal, year, or DOI">
        </label>
        <span id="publicationCount" aria-live="polite">${publicationCountLabel(page)}</span>
      </div>
      <div id="publicPublicationList" class="public-publication-list">
        ${publicationResultsMarkup(page)}
      </div>
      <nav id="publicationPagination" class="publication-pagination" aria-label="Publication pages">
        ${publicationPaginationMarkup(page)}
      </nav>
    </section>`;
}

function profileMapWorldSize(zoom) {
  return PROFILE_MAP_TILE_SIZE * (2 ** zoom);
}

function profileLonLatToTile(longitude, latitude, zoom) {
  const boundedLatitude = Math.max(-85.0511, Math.min(85.0511, Number(latitude)));
  const latRad = boundedLatitude * Math.PI / 180;
  const scale = 2 ** zoom;
  return {
    x: (Number(longitude) + 180) / 360 * scale,
    y: (1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * scale,
  };
}

function clampProfileMapOrigin(origin, zoom) {
  const world = profileMapWorldSize(zoom);
  return {
    x: Math.max(0, Math.min(Math.max(0, world - PROFILE_MAP_WIDTH), origin.x)),
    y: Math.max(0, Math.min(Math.max(0, world - PROFILE_MAP_HEIGHT), origin.y)),
  };
}

function defaultProfileMapOrigin(zoom = PROFILE_MAP_DEFAULT_ZOOM) {
  const own = state.collaborationMap.data?.nodes?.find((node) => node.own)
    || state.collaborationMap.data?.own;
  const world = profileMapWorldSize(zoom);
  if (!Number.isFinite(Number(own?.longitude)) || !Number.isFinite(Number(own?.latitude))) {
    return {
      x: (world - PROFILE_MAP_WIDTH) / 2,
      y: (world - PROFILE_MAP_HEIGHT) / 2,
    };
  }
  const tile = profileLonLatToTile(own.longitude, own.latitude, zoom);
  return clampProfileMapOrigin({
    x: tile.x * PROFILE_MAP_TILE_SIZE - PROFILE_MAP_WIDTH / 2,
    y: tile.y * PROFILE_MAP_TILE_SIZE - PROFILE_MAP_HEIGHT / 2,
  }, zoom);
}

function profileMapExtent() {
  const zoom = state.collaborationMap.zoom || PROFILE_MAP_DEFAULT_ZOOM;
  const origin = clampProfileMapOrigin(
    state.collaborationMap.origin || defaultProfileMapOrigin(zoom),
    zoom,
  );
  state.collaborationMap.origin = origin;
  return {
    zoom,
    origin: {
      x: origin.x / PROFILE_MAP_TILE_SIZE,
      y: origin.y / PROFILE_MAP_TILE_SIZE,
    },
  };
}

function profileMapPoint(node, extent) {
  const tile = profileLonLatToTile(node.longitude, node.latitude, extent.zoom);
  return {
    x: (tile.x - extent.origin.x) * PROFILE_MAP_TILE_SIZE,
    y: (tile.y - extent.origin.y) * PROFILE_MAP_TILE_SIZE,
  };
}

function profileMapTiles(extent) {
  const scale = 2 ** extent.zoom;
  const startX = Math.floor(extent.origin.x);
  const endX = Math.ceil(extent.origin.x + PROFILE_MAP_WIDTH / PROFILE_MAP_TILE_SIZE);
  const startY = Math.floor(extent.origin.y);
  const endY = Math.ceil(extent.origin.y + PROFILE_MAP_HEIGHT / PROFILE_MAP_TILE_SIZE);
  const tiles = [];
  for (let x = startX; x <= endX; x += 1) {
    for (let y = startY; y <= endY; y += 1) {
      if (x < 0 || x >= scale || y < 0 || y >= scale) continue;
      tiles.push(`
        <image
          href="https://tile.openstreetmap.org/${extent.zoom}/${x}/${y}.png"
          x="${((x - extent.origin.x) * PROFILE_MAP_TILE_SIZE).toFixed(1)}"
          y="${((y - extent.origin.y) * PROFILE_MAP_TILE_SIZE).toFixed(1)}"
          width="${PROFILE_MAP_TILE_SIZE}"
          height="${PROFILE_MAP_TILE_SIZE}"
          preserveAspectRatio="none"
        ></image>`);
    }
  }
  return tiles.join("");
}

function collaboratorTooltip(node) {
  const authors = (node.authors || []).slice(0, 8).join(", ");
  const more = (node.authors || []).length > 8 ? `, +${node.authors.length - 8} more` : "";
  return `
    <strong>${escapeHtml(node.name)}</strong>
    ${node.country ? `<span>${escapeHtml(node.country)}</span>` : ""}
    ${node.publication_count ? `<span>${formatNumber(node.publication_count)} publication links</span>` : ""}
    ${authors ? `<span>${escapeHtml(authors + more)}</span>` : ""}`;
}

function publicCollaborationMapMarkup(data) {
  const nodes = Array.isArray(data.nodes) ? data.nodes : [];
  const own = nodes.find((node) => node.own) || data.own;
  const extent = profileMapExtent();
  const nodeById = Object.fromEntries(nodes.map((node) => [node.id, node]));
  const edges = (data.edges || []).map((edge) => ({
    ...edge,
    sourceNode: nodeById[edge.source],
    targetNode: nodeById[edge.target],
  })).filter((edge) => edge.sourceNode && edge.targetNode).map((edge) => {
    const source = profileMapPoint(edge.sourceNode, extent);
    const target = profileMapPoint(edge.targetNode, extent);
    const middle = (source.x + target.x) / 2;
    const lift = Math.min(85, Math.max(22, Math.abs(target.x - source.x) * .12));
    const width = Math.min(5, .7 + Number(edge.weight || 1) * .4);
    return `<path d="M${source.x.toFixed(1)} ${source.y.toFixed(1)} Q${middle.toFixed(1)} ${(Math.min(source.y, target.y) - lift).toFixed(1)} ${target.x.toFixed(1)} ${target.y.toFixed(1)}" style="stroke-width:${width.toFixed(1)}"></path>`;
  }).join("");
  const markers = nodes.map((node) => {
    const point = profileMapPoint(node, extent);
    const size = node.own ? 18 : Math.min(20, 8 + Math.sqrt(Number(node.publication_count || 1)) * 2.2);
    return `
      <button
        class="collaborator-marker ${node.own ? "own" : ""}"
        type="button"
        style="left:${(point.x / PROFILE_MAP_WIDTH * 100).toFixed(2)}%;top:${(point.y / PROFILE_MAP_HEIGHT * 100).toFixed(2)}%;width:${size.toFixed(1)}px;height:${size.toFixed(1)}px"
        aria-label="${escapeHtml(node.name)}"
      ><span>${collaboratorTooltip(node)}</span></button>`;
  }).join("");
  return `
    <svg class="public-osm-tiles" viewBox="0 0 ${PROFILE_MAP_WIDTH} ${PROFILE_MAP_HEIGHT}" preserveAspectRatio="none" aria-hidden="true">
      ${profileMapTiles(extent)}
    </svg>
    <svg class="public-map-overlay" viewBox="0 0 ${PROFILE_MAP_WIDTH} ${PROFILE_MAP_HEIGHT}" preserveAspectRatio="none" aria-hidden="true">
      <g class="public-map-edges">${edges}</g>
    </svg>
    ${markers}
    <div class="public-map-controls" aria-label="Map controls">
      <button type="button" data-map-zoom="in" aria-label="Zoom in">+</button>
      <button type="button" data-map-zoom="out" aria-label="Zoom out">−</button>
      <button type="button" data-map-zoom="reset" aria-label="Reset map">Reset</button>
    </div>
    <a class="public-osm-credit" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap</a>`;
}

function renderCollaborators(profile) {
  const data = profile.collaborators || {};
  const nodes = Array.isArray(data.nodes) ? data.nodes : [];
  const own = nodes.find((node) => node.own) || data.own;
  const collaborators = nodes.filter((node) => !node.own);
  if (!own || !collaborators.length) {
    return `
      <section class="profile-block collaborators-block" data-profile-block="collaborators">
        ${blockHeader("collaborators", "Publication affiliations")}
        <p class="empty-copy">No public collaboration geography is available yet.</p>
      </section>`;
  }
  state.collaborationMap.data = data;
  if (!state.collaborationMap.origin) {
    state.collaborationMap.zoom = PROFILE_MAP_DEFAULT_ZOOM;
    state.collaborationMap.origin = defaultProfileMapOrigin(PROFILE_MAP_DEFAULT_ZOOM);
  }
  return `
    <section class="profile-block collaborators-block" data-profile-block="collaborators">
      ${blockHeader("collaborators", "Publication affiliations")}
      <div id="publicCollaborationMap" class="public-collaboration-map">
        ${publicCollaborationMapMarkup(data)}
      </div>
      <div class="collaboration-summary">
        <span><strong>${formatNumber(data.institution_count)}</strong> institutions</span>
        <span><strong>${formatNumber(data.publication_links)}</strong> publication links</span>
        <span>Centered on ${escapeHtml(own.name)}</span>
      </div>
    </section>`;
}

function renderProfile() {
  const profile = state.profile;
  if (!profile) return;
  renderHero(profile);
  const renderers = {
    bio: renderBio,
    metrics: renderMetrics,
    publications: renderPublications,
    collaborators: renderCollaborators,
  };
  const blocks = normalizedBlocks(profile)
    .filter((item) => item.visible)
    .filter((item) => !requestedBlock || item.key === requestedBlock);
  $("#profileBlocks").innerHTML = blocks.map((item) => renderers[item.key](profile)).join("")
    || '<p class="empty-profile">This profile does not currently publish any blocks.</p>';
  $("#profileUpdated").textContent = profile.updated_at
    ? `Updated ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(profile.updated_at))}`
    : "";
  if (embedded && requestedBlock) $(".profile-footer").hidden = true;
  bindProfileInteractions();
}

function bindCitationDetails() {
  const years = state.profile?.metrics?.by_year || [];
  const detail = $("#publicCitationDetail");
  if (!detail) return;
  const items = document.querySelectorAll("[data-citation-index]");
  const hide = () => {
    detail.hidden = true;
    items.forEach((item) => item.removeAttribute("aria-current"));
  };
  const show = (button) => {
    const row = years[Number(button.dataset.citationIndex)];
    if (!row) return;
    detail.innerHTML = `
      <strong>${escapeHtml(row.year)}</strong>
      <span>${formatNumber(row.citations)} citations received</span>
      <span>${formatNumber(row.first_last_author_citations)} from first/last-author work</span>
      <span>${formatNumber(row.publications_published)} publications published</span>
      <span>${row.impact_factor_count ? `${Number(row.impact_factor_sum || 0).toLocaleString()} combined journal Impact Factor` : "No journal Impact Factor data"}</span>`;
    detail.hidden = false;
    items.forEach((item) => item.toggleAttribute("aria-current", item === button));
  };
  items.forEach((button) => {
    button.addEventListener("mouseenter", () => show(button));
    button.addEventListener("mouseleave", () => {
      if (document.activeElement !== button) hide();
    });
    button.addEventListener("focus", () => show(button));
    button.addEventListener("blur", hide);
    button.addEventListener("click", () => show(button));
  });
}

function bindPublicationSearch() {
  const input = $("#publicationSearch");
  if (!input) return;
  input.addEventListener("input", () => {
    state.publications.query = input.value;
    state.publications.page = 0;
    renderPublicationResults();
  });
  $("#publicationPagination")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-publication-page]");
    if (!button || button.disabled) return;
    state.publications.page += button.dataset.publicationPage === "next" ? 1 : -1;
    renderPublicationResults();
    $(".publications-block")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function renderPublicationResults() {
  const page = publicationPage();
  const list = $("#publicPublicationList");
  const count = $("#publicationCount");
  const pagination = $("#publicationPagination");
  if (!list || !count || !pagination) return;
  list.innerHTML = publicationResultsMarkup(page);
  count.textContent = publicationCountLabel(page);
  pagination.innerHTML = publicationPaginationMarkup(page);
}

function rerenderPublicCollaborationMap() {
  const container = $("#publicCollaborationMap");
  if (!container || !state.collaborationMap.data) return;
  container.innerHTML = publicCollaborationMapMarkup(state.collaborationMap.data);
}

function profileMapViewPoint(event, container) {
  const rect = container.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) / rect.width * PROFILE_MAP_WIDTH,
    y: (event.clientY - rect.top) / rect.height * PROFILE_MAP_HEIGHT,
  };
}

function zoomPublicCollaborationMap(direction, focal = {
  x: PROFILE_MAP_WIDTH / 2,
  y: PROFILE_MAP_HEIGHT / 2,
}) {
  const oldZoom = state.collaborationMap.zoom || PROFILE_MAP_DEFAULT_ZOOM;
  const nextZoom = Math.max(
    PROFILE_MAP_MIN_ZOOM,
    Math.min(PROFILE_MAP_MAX_ZOOM, oldZoom + direction),
  );
  if (nextZoom === oldZoom) return;
  const oldOrigin = state.collaborationMap.origin || defaultProfileMapOrigin(oldZoom);
  const scale = profileMapWorldSize(nextZoom) / profileMapWorldSize(oldZoom);
  state.collaborationMap.zoom = nextZoom;
  state.collaborationMap.origin = clampProfileMapOrigin({
    x: (oldOrigin.x + focal.x) * scale - focal.x,
    y: (oldOrigin.y + focal.y) * scale - focal.y,
  }, nextZoom);
  rerenderPublicCollaborationMap();
}

function resetPublicCollaborationMap() {
  state.collaborationMap.zoom = PROFILE_MAP_DEFAULT_ZOOM;
  state.collaborationMap.origin = defaultProfileMapOrigin(PROFILE_MAP_DEFAULT_ZOOM);
  rerenderPublicCollaborationMap();
}

function bindPublicCollaborationMap() {
  const container = $("#publicCollaborationMap");
  if (!container || container.dataset.mapEventsBound) return;
  container.dataset.mapEventsBound = "true";
  container.addEventListener("click", (event) => {
    const button = event.target.closest("[data-map-zoom]");
    if (!button) return;
    if (button.dataset.mapZoom === "in") zoomPublicCollaborationMap(1);
    if (button.dataset.mapZoom === "out") zoomPublicCollaborationMap(-1);
    if (button.dataset.mapZoom === "reset") resetPublicCollaborationMap();
  });
  container.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomPublicCollaborationMap(
      event.deltaY < 0 ? 1 : -1,
      profileMapViewPoint(event, container),
    );
  }, { passive: false });
  container.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".collaborator-marker, .public-map-controls, .public-osm-credit")) return;
    container.setPointerCapture(event.pointerId);
    state.collaborationMap.drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: {
        ...(state.collaborationMap.origin
          || defaultProfileMapOrigin(state.collaborationMap.zoom || PROFILE_MAP_DEFAULT_ZOOM)),
      },
    };
    container.classList.add("dragging");
  });
  container.addEventListener("pointermove", (event) => {
    const drag = state.collaborationMap.drag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const rect = container.getBoundingClientRect();
    const dx = (event.clientX - drag.startX) / rect.width * PROFILE_MAP_WIDTH;
    const dy = (event.clientY - drag.startY) / rect.height * PROFILE_MAP_HEIGHT;
    const zoom = state.collaborationMap.zoom || PROFILE_MAP_DEFAULT_ZOOM;
    state.collaborationMap.origin = clampProfileMapOrigin({
      x: drag.origin.x - dx,
      y: drag.origin.y - dy,
    }, zoom);
    rerenderPublicCollaborationMap();
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

function bindProfileInteractions() {
  bindCitationDetails();
  bindPublicationSearch();
  bindPublicCollaborationMap();
  document.querySelectorAll("[data-embed-block]").forEach((button) => {
    button.addEventListener("click", () => openEmbedDialog(button.dataset.embedBlock));
  });
}

function renderLayoutDraft() {
  const list = $("#layoutBlockList");
  list.innerHTML = state.layoutDraft.map((item, index) => `
    <article class="layout-block-row" data-layout-key="${item.key}">
      <label>
        <input type="checkbox" ${item.visible ? "checked" : ""}>
        <span><strong>${escapeHtml(BLOCK_LABELS[item.key])}</strong><small>${item.visible ? "Public" : "Hidden"}</small></span>
      </label>
      <div>
        <button type="button" data-move="-1" ${index === 0 ? "disabled" : ""} aria-label="Move ${escapeHtml(BLOCK_LABELS[item.key])} up">↑</button>
        <button type="button" data-move="1" ${index === state.layoutDraft.length - 1 ? "disabled" : ""} aria-label="Move ${escapeHtml(BLOCK_LABELS[item.key])} down">↓</button>
      </div>
    </article>`).join("");
  list.querySelectorAll(".layout-block-row").forEach((row) => {
    const key = row.dataset.layoutKey;
    row.querySelector("input").addEventListener("change", (event) => {
      const block = state.layoutDraft.find((item) => item.key === key);
      block.visible = event.target.checked;
      renderLayoutDraft();
    });
    row.querySelectorAll("[data-move]").forEach((button) => {
      button.addEventListener("click", () => {
        const index = state.layoutDraft.findIndex((item) => item.key === key);
        const next = index + Number(button.dataset.move);
        if (next < 0 || next >= state.layoutDraft.length) return;
        [state.layoutDraft[index], state.layoutDraft[next]] = [state.layoutDraft[next], state.layoutDraft[index]];
        renderLayoutDraft();
      });
    });
  });
}

function openLayoutDialog() {
  state.layoutDraft = normalizedBlocks(state.profile).map((item) => ({ ...item }));
  $("#profileTitleInput").value = publicProfileTitle(state.profile);
  renderLayoutDraft();
  $("#layoutDialog").showModal();
}

function embedHeight(target) {
  return {
    bio: 420,
    metrics: 620,
    publications: 760,
    collaborators: 610,
  }[target] || 1200;
}

function updateEmbedCode() {
  const theme = document.querySelector('input[name="embed_theme"]:checked')?.value || "native";
  const query = new URLSearchParams({ theme });
  if (state.embedTarget) query.set("block", state.embedTarget);
  const src = `${window.location.origin}/embed/${encodeURIComponent(slug)}?${query}`;
  const profileTitle = publicProfileTitle(state.profile);
  const title = state.embedTarget
    ? `${BLOCK_LABELS[state.embedTarget]} — ${profileTitle}`
    : `${profileTitle} — academic profile`;
  $("#embedCode").value = `<iframe src="${src}" title="${title.replaceAll('"', "&quot;")}" loading="lazy" style="width:100%;height:${embedHeight(state.embedTarget)}px;border:0;" referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
  $("#embedPreview").href = src;
}

function openEmbedDialog(target = "") {
  state.embedTarget = target;
  $("#embedDialogTitle").textContent = target ? `Embed ${BLOCK_LABELS[target]}` : "Embed entire profile";
  document.querySelector('input[name="embed_theme"][value="native"]').checked = true;
  $("#embedMessage").textContent = "";
  updateEmbedCode();
  $("#embedDialog").showModal();
}

async function copyEmbedCode() {
  const textarea = $("#embedCode");
  try {
    await navigator.clipboard.writeText(textarea.value);
  } catch {
    textarea.select();
    document.execCommand("copy");
  }
  $("#embedMessage").textContent = "Embed code copied.";
}

async function detectOwner() {
  if (embedded) return;
  try {
    const payload = await requestJson("/api/profile/manage");
    if (payload.profile?.slug !== slug) return;
    state.owner = true;
    $("#ownerToolbar").hidden = false;
    renderProfile();
  } catch {
    state.owner = false;
  }
}

function bindOwnerControls() {
  if (embedded) return;
  $("#editProfileLayout").addEventListener("click", openLayoutDialog);
  $("#refreshProfile").addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    $("#ownerMessage").textContent = "Refreshing from your CV…";
    try {
      await requestJson("/api/profile/refresh", { method: "POST" });
      window.location.reload();
    } catch (error) {
      $("#ownerMessage").textContent = error.message;
      event.currentTarget.disabled = false;
    }
  });
  $("#embedFullProfile").addEventListener("click", () => openEmbedDialog(""));
  $("#closeLayoutDialog").addEventListener("click", () => $("#layoutDialog").close());
  $("#closeEmbedDialog").addEventListener("click", () => $("#embedDialog").close());
  $("#layoutForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.currentTarget.querySelector('button[type="submit"]');
    submit.disabled = true;
    $("#ownerMessage").textContent = "Saving profile…";
    try {
      const profileTitle = $("#profileTitleInput").value.replace(/\s+/g, " ").trim();
      const header = await requestJson("/api/profile/header", {
        method: "PUT",
        body: JSON.stringify({ profile_title: profileTitle }),
      });
      const layout = await requestJson("/api/profile/blocks", {
        method: "PUT",
        body: JSON.stringify({ blocks: state.layoutDraft }),
      });
      state.profile.profile_title = header.profile_title;
      state.profile.blocks = layout.blocks;
      renderProfile();
      $("#layoutDialog").close();
      $("#ownerMessage").textContent = "Profile saved.";
    } catch (error) {
      $("#ownerMessage").textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
  $("#unpublishProfile").addEventListener("click", async () => {
    if (!window.confirm("Remove this public profile and all of its embeds? Your private CV remains untouched.")) return;
    await requestJson(`/api/profiles/${encodeURIComponent(slug)}`, { method: "DELETE" });
    window.location.assign("/");
  });
  document.querySelectorAll('input[name="embed_theme"]').forEach((input) => {
    input.addEventListener("change", updateEmbedCode);
  });
  $("#copyEmbedCode").addEventListener("click", copyEmbedCode);
}

async function initialize() {
  bindOwnerControls();
  state.profile = await requestJson(`/api/public/${encodeURIComponent(slug)}`);
  renderProfile();
  await detectOwner();
}

initialize().catch((error) => {
  $("#profileBlocks").innerHTML = `<p class="empty-profile">${escapeHtml(error.message)}</p>`;
});
