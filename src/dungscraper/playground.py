"""Small playground served by the API. It calls the same endpoints agents use."""

PLAYGROUND_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>DungScraper</title>
<style>
  :root {
    --ink: #1c140f;
    --cream: #f6f0e6;
    --paper: #fffaf3;
    --ember: #e4572e;
    --ember-deep: #b33b18;
    --line: #e4d8c8;
    --muted: #6f6258;
    --good: #2f6f4e;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--ink);
    color: var(--cream);
    font-family: "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
  }
  header, main { width: min(1120px, calc(100% - 32px)); margin: 0 auto; }
  header { display: flex; justify-content: space-between; align-items: baseline; padding: 28px 0 8px; gap: 16px; }
  .mark { font-size: 28px; letter-spacing: -0.04em; }
  .mark span { color: var(--ember); }
  nav a { color: var(--cream); margin-left: 16px; }
  h1 { font-weight: 500; font-size: clamp(32px, 5vw, 56px); letter-spacing: -0.04em; margin: 12px 0; }
  .lede { max-width: 640px; color: #eadfce; font-size: 18px; line-height: 1.45; }
  .panel {
    background: var(--paper);
    color: var(--ink);
    border-radius: 18px;
    padding: 18px;
    margin: 22px 0 40px;
    box-shadow: 0 20px 50px rgba(0,0,0,.28);
  }
  .tabs { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
  .tabs button, .samples button, button.go {
    font-family: ui-sans-serif, system-ui, sans-serif;
    border: 1px solid var(--line);
    background: transparent;
    border-radius: 999px;
    padding: 8px 12px;
    cursor: pointer;
  }
  .tabs button.active, button.go { background: var(--ink); color: var(--cream); border-color: var(--ink); }
  button.go { background: var(--ember); border-color: var(--ember); font-weight: 650; }
  button.go:disabled { opacity: .6; cursor: wait; }
  label { display: block; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 12px; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); margin: 10px 0 6px; }
  input, select, textarea {
    width: 100%;
    font: 16px/1.4 ui-sans-serif, system-ui, sans-serif;
    padding: 12px 14px;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: white;
    color: var(--ink);
  }
  .row { display: grid; grid-template-columns: 1fr 160px auto; gap: 10px; align-items: end; }
  .checks { display: flex; gap: 14px; flex-wrap: wrap; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 14px; margin-top: 10px; }
  .samples { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .meta { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0; }
  .pill { font-family: ui-sans-serif, system-ui, sans-serif; font-size: 12px; background: #f3e7da; border-radius: 999px; padding: 5px 10px; }
  .split { display: grid; grid-template-columns: 1.4fr .8fr; gap: 16px; }
  pre, .md {
    white-space: pre-wrap;
    word-break: break-word;
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 14px;
    min-height: 180px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 13px;
    line-height: 1.5;
  }
  .md h1, .md h2, .md h3 { font-family: Georgia, serif; margin: 0 0 8px; }
  .md p { margin: 0 0 10px; }
  .error { background: #fde8e2; color: var(--ember-deep); border-radius: 12px; padding: 12px 14px; font-family: ui-sans-serif, system-ui, sans-serif; }
  .hidden { display: none; }
  footer { color: #b5a89a; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13px; padding-bottom: 32px; }
  @media (max-width: 800px) {
    .row, .split { grid-template-columns: 1fr; }
    header { flex-direction: column; }
  }
</style>
</head>
<body>
<header>
  <div class="mark">Dung<span>Scraper</span></div>
  <nav>
    <a href="/docs">API docs</a>
    <a href="/health">Health</a>
  </nav>
</header>
<main>
  <h1>Pages, ready for an agent.</h1>
  <p class="lede">Scrape a URL into Markdown, map a site, or search and read the hits. The same calls are on the REST API and the MCP server.</p>
  <section class="panel">
    <div class="tabs">
      <button class="active" data-tab="scrape" type="button">Scrape</button>
      <button data-tab="map" type="button">Map</button>
      <button data-tab="search" type="button">Search</button>
    </div>
    <label for="key">API key, if the server requires one</label>
    <input id="key" placeholder="dungscraper-dev-key" autocomplete="off" />
    <div id="scrape">
      <label for="url">URL</label>
      <div class="row">
        <input id="url" value="https://example.com" />
        <select id="render">
          <option value="auto">Auto render</option>
          <option value="http">HTTP only</option>
          <option value="browser">Browser</option>
        </select>
        <button class="go" id="go" type="button">Scrape</button>
      </div>
      <div class="checks">
        <label><input type="checkbox" id="fmt-md" checked /> Markdown</label>
        <label><input type="checkbox" id="fmt-meta" checked /> Metadata</label>
        <label><input type="checkbox" id="fmt-links" /> Links</label>
        <label><input type="checkbox" id="fmt-html" /> Clean HTML</label>
      </div>
      <div class="samples">
        <button type="button" data-sample="https://example.com">example.com</button>
        <button type="button" data-sample="https://en.wikipedia.org/wiki/Web_scraping">Wikipedia</button>
        <button type="button" data-sample="https://quotes.toscrape.com/js/">JS quotes</button>
      </div>
    </div>
    <div id="map" class="hidden">
      <label for="map-url">Site</label>
      <div class="row">
        <input id="map-url" value="https://example.com" />
        <input id="map-search" placeholder="filter" />
        <button class="go" id="map-go" type="button">Map</button>
      </div>
    </div>
    <div id="search" class="hidden">
      <label for="query">Query</label>
      <div class="row">
        <input id="query" value="self hosted web crawler" />
        <select id="provider">
          <option value="auto">Auto</option>
          <option value="duckduckgo">DuckDuckGo</option>
          <option value="searxng">SearXNG</option>
          <option value="brave">Brave</option>
          <option value="serper">Serper</option>
        </select>
        <button class="go" id="search-go" type="button">Search</button>
      </div>
    </div>
    <div id="status"></div>
    <div id="error" class="error hidden"></div>
    <div class="split" id="output"></div>
  </section>
  <footer>robots.txt is respected by default. Private network addresses are blocked unless you turn that on.</footer>
</main>
<script>
const keyInput = document.querySelector("#key");
keyInput.value = localStorage.getItem("dungscraper-key") || "";
keyInput.addEventListener("change", () => localStorage.setItem("dungscraper-key", keyInput.value.trim()));
function headers() {
  const headers = { "Content-Type": "application/json" };
  const key = keyInput.value.trim();
  if (key) headers.Authorization = "Bearer " + key;
  return headers;
}
function showError(message) {
  const box = document.querySelector("#error");
  box.textContent = message;
  box.classList.remove("hidden");
}
function clearError() { document.querySelector("#error").classList.add("hidden"); }
document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    ["scrape", "map", "search"].forEach((name) => {
      document.querySelector("#" + name).classList.toggle("hidden", name !== button.dataset.tab);
    });
  });
});
document.querySelectorAll("[data-sample]").forEach((button) => {
  button.addEventListener("click", () => { document.querySelector("#url").value = button.dataset.sample; });
});
function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
function renderMarkdown(markdown) {
  const escaped = escapeHtml(markdown || "");
  const withCode = escaped.replace(/```([\s\S]*?)```/g, (_, code) => "<pre>" + code + "</pre>");
  return withCode
    .replace(/^### (.*)$/gm, "<h3>$1</h3>")
    .replace(/^## (.*)$/gm, "<h2>$1</h2>")
    .replace(/^# (.*)$/gm, "<h1>$1</h1>")
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2">$1</a>')
    .replace(/\n{2,}/g, "</p><p>");
}
async function post(path, body, button) {
  clearError();
  button.disabled = true;
  const started = performance.now();
  try {
    const response = await fetch(path, { method: "POST", headers: headers(), body: JSON.stringify(body) });
    const payload = await response.json();
    if (!response.ok || payload.success === false) {
      const error = payload.error || {};
      showError((error.code ? error.code + ": " : "") + (error.message || response.statusText));
      document.querySelector("#output").innerHTML = "";
      document.querySelector("#status").innerHTML = "";
      return;
    }
    return { data: payload.data, ms: Math.round(performance.now() - started) };
  } catch (error) {
    showError(error.message || "Request failed");
  } finally {
    button.disabled = false;
  }
}
document.querySelector("#go").addEventListener("click", async () => {
  const formats = [];
  if (document.querySelector("#fmt-md").checked) formats.push("markdown");
  if (document.querySelector("#fmt-meta").checked) formats.push("metadata");
  if (document.querySelector("#fmt-links").checked) formats.push("links");
  if (document.querySelector("#fmt-html").checked) formats.push("html");
  const result = await post("/v1/scrape", {
    url: document.querySelector("#url").value.trim(),
    render: document.querySelector("#render").value,
    formats
  }, document.querySelector("#go"));
  if (!result) return;
  const data = result.data;
  const pills = [
    data.render_mode, (data.timings_ms && data.timings_ms.total_ms ? data.timings_ms.total_ms + " ms fetch" : result.ms + " ms"),
    data.status_code ? "HTTP " + data.status_code : ""
  ].filter(Boolean);
  document.querySelector("#status").innerHTML = '<div class="meta">' + pills.map((pill) => '<span class="pill">' + escapeHtml(String(pill)) + "</span>").join("") + "</div>";
  const links = (data.links || []).slice(0, 30).map((link) => escapeHtml(link.url)).join("\n");
  const meta = data.metadata ? JSON.stringify(data.metadata, null, 2) : "";
  document.querySelector("#output").innerHTML =
    '<div><div class="md">' + renderMarkdown(data.markdown || data.html || "") + '</div></div>' +
    '<div><pre>' + escapeHtml(meta + (links ? "\n\n" + links : "")) + "</pre></div>";
});
document.querySelector("#map-go").addEventListener("click", async () => {
  const result = await post("/v1/map", {
    url: document.querySelector("#map-url").value.trim(),
    search: document.querySelector("#map-search").value.trim() || null,
    limit: 100
  }, document.querySelector("#map-go"));
  if (!result) return;
  const lines = result.data.links.map((link) => (link.source + "  " + link.url + (link.title ? "  " + link.title : ""))).join("\n");
  document.querySelector("#status").innerHTML = '<div class="meta"><span class="pill">' + result.data.links.length + ' urls</span><span class="pill">' + result.ms + ' ms</span></div>';
  document.querySelector("#output").innerHTML = "<pre>" + escapeHtml(lines || "No URLs matched.") + "</pre>";
});
document.querySelector("#search-go").addEventListener("click", async () => {
  const result = await post("/v1/search", {
    query: document.querySelector("#query").value.trim(),
    provider: document.querySelector("#provider").value,
    limit: 3,
    scrape: true
  }, document.querySelector("#search-go"));
  if (!result) return;
  document.querySelector("#status").innerHTML = '<div class="meta"><span class="pill">' + escapeHtml(result.data.provider) + '</span><span class="pill">' + result.ms + ' ms</span></div>';
  const html = result.data.results.map((item) => {
    return "<h2>" + escapeHtml(item.title || item.url) + "</h2><p><a href='" + escapeHtml(item.url) + "'>" + escapeHtml(item.url) + "</a></p>" +
      (item.error ? "<p>" + escapeHtml(item.error) + "</p>" : "<div class='md'>" + renderMarkdown((item.markdown || "").slice(0, 1500)) + "</div>");
  }).join("");
  document.querySelector("#output").innerHTML = "<div>" + html + "</div>";
});
</script>
</body>
</html>
"""
