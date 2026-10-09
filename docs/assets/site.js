"use strict";

// Links and all example content work without JavaScript. Enhancement swaps
// one compact panel in place; no routing framework or remote model calls.
const tabs = Array.from(document.querySelectorAll(".demo-tabs a"));
const panels = Array.from(document.querySelectorAll(".demo-panel"));
function activateDemo(id, focus = false) {
  if (!panels.some((p) => p.id === id)) id = panels.length ? panels[0].id : id;
  panels.forEach((p) => { p.hidden = p.id !== id; });
  tabs.forEach((t) => {
    const selected = t.hash === "#" + id;
    t.setAttribute("aria-selected", String(selected));
    t.tabIndex = selected ? 0 : -1;
    if (selected && focus) t.focus({ preventScroll: true });
    // On a phone each group is one scrolling row; bring the selected tab into it without moving the page.
    const row = t.parentElement;
    if (selected && row.scrollWidth > row.clientWidth) row.scrollLeft = Math.max(0, t.offsetLeft - 24);
  });
}
if (tabs.length) {
  // Tabs come in labelled groups; each group is a tablist, and the arrow keys move across all of them.
  const lists = document.querySelectorAll(".demo-tabs [data-tablist]");
  (lists.length ? lists : [document.querySelector(".demo-tabs")]).forEach((list) => list.setAttribute("role", "tablist"));
  tabs.forEach((tab, i) => {
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", tab.hash.slice(1));
    tab.addEventListener("click", (event) => {
      event.preventDefault();
      if (location.hash !== tab.hash) history.pushState(null, "", tab.hash);
      activateDemo(tab.hash.slice(1));
    });
    tab.addEventListener("keydown", (event) => {
      const next = event.key === "ArrowRight" ? (i + 1) % tabs.length
        : event.key === "ArrowLeft" ? (i + tabs.length - 1) % tabs.length
        : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
      if (next < 0) return;
      event.preventDefault();
      history.replaceState(null, "", tabs[next].hash);
      activateDemo(tabs[next].hash.slice(1), true);
    });
  });
  panels.forEach((p) => { p.setAttribute("role", "tabpanel"); p.tabIndex = 0; });
  activateDemo(location.hash.slice(1));
  window.addEventListener("popstate", () => activateDemo(location.hash.slice(1)));
}

// One <select> shows one of several sibling articles. Every article stays in the
// page for no-JS readers and for print.
const choices = [];
function bindChoice(id, attribute, onChange) {
  const select = document.getElementById(id);
  if (!select) return null;
  const sync = () => {
    document.querySelectorAll("[" + attribute + "]").forEach((row) => {
      row.hidden = row.getAttribute(attribute) !== select.value;
    });
    // A copy message belongs to the article it was copied from.
    (select.closest(".demo-panel") || document).querySelectorAll(".copy-status").forEach((s) => { s.textContent = ""; });
    if (onChange) onChange(select.value);
  };
  select.addEventListener("change", sync);
  sync();
  const choice = { select, attribute, sync };
  choices.push(choice);
  return choice;
}
bindChoice("record-choice", "data-record");
bindChoice("help-choice", "data-help");
bindChoice("example-choice", "data-example");
bindChoice("path-choice", "data-path");
bindChoice("release-choice", "data-release");
bindChoice("collection-choice", "data-collection");
bindChoice("address-choice", "data-address");
bindChoice("cite-choice", "data-cite");

// Timeline filters: two selects narrow one list. "all" shows every row.
const timelineSide = document.getElementById("timeline-side");
const timelineTopic = document.getElementById("timeline-topic");
if (timelineSide && timelineTopic) {
  const rows = Array.from(document.querySelectorAll(".statement-timeline > li"));
  const count = document.getElementById("timeline-count");
  const filter = () => {
    let shown = 0;
    rows.forEach((row) => {
      const visible = (timelineSide.value === "all" || row.dataset.side === timelineSide.value)
        && (timelineTopic.value === "all" || row.dataset.topic === timelineTopic.value);
      row.hidden = !visible;
      if (visible) shown += 1;
    });
    if (count) count.textContent = "Showing " + shown + " of " + rows.length + " entries.";
  };
  timelineSide.addEventListener("change", filter);
  timelineTopic.addEventListener("change", filter);
  filter();
}

// Key dates: the page states each date; the reader's own clock supplies the distance to it.
document.querySelectorAll(".days[data-date]").forEach((span) => {
  const parts = span.dataset.date.split("-").map(Number);
  if (parts.length !== 3 || parts.some((n) => !Number.isInteger(n))) return;
  const now = new Date();
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Math.round((Date.UTC(parts[0], parts[1] - 1, parts[2]) - today) / 86400000);
  span.textContent = days === 0 ? "today" : days > 0
    ? "in " + days + (days === 1 ? " day" : " days")
    : -days + (days === -1 ? " day ago" : " days ago");
});

// Query builder: the rules portal_query_draft applies, run on the reader's input. Nothing is sent.
const builder = document.getElementById("query-builder");
if (builder) {
  const phrase = document.getElementById("qb-phrase");
  const words = document.getElementById("qb-words");
  const box = document.getElementById("qb-box");
  const out = document.getElementById("qb-query");
  const call = document.getElementById("qb-call");
  const copy = document.getElementById("qb-copy");
  const status = document.getElementById("qb-status");
  const copyStatus = document.getElementById("qb-copy-status");
  // Same normalization as the tool: quotation marks dropped, spaces collapsed.
  const clean = (text) => text.replace(/["\u201c\u201d]/g, "").replace(/\s+/g, " ").trim();
  const update = () => {
    const p = clean(phrase.value);
    const w = clean(words.value).split(" ").filter(Boolean);
    const b = clean(box.value);
    const operator = w.some((t) => t.includes(":"));
    const terms = operator ? [] : (p ? ['"' + p + '"'] : []).concat(w);
    const query = terms.length ? terms.concat(["extension:pdf"]).join(" ") : "";
    if (copyStatus) copyStatus.textContent = "";
    out.textContent = query || "(give a phrase or some words)";
    copy.dataset.copy = query;
    copy.disabled = !query;
    const args = {};
    if (p) args.phrase = p;
    if (w.length) args.words = w.join(" ");
    if (b) args.box = b;
    call.textContent = "portal_query_draft " + JSON.stringify(args);
    status.textContent = operator ? "Other words are plain terms; the draft adds extension:pdf itself."
      : !query ? "Give a phrase or some words to build a portal query."
      : terms.length === 1 && !p ? "One loose word matches broadly; a quoted phrase is more precise."
      : "A quoted phrase matches those words in that order. extension:pdf keeps results to the documents themselves.";
  };
  [phrase, words, box].forEach((input) => input.addEventListener("input", update));
  builder.addEventListener("submit", (event) => event.preventDefault());
  update();
}

function revealHash() {
  const id = location.hash.slice(1);
  if (!id) return;
  if (panels.some((p) => p.id === id)) activateDemo(id);
  let node = document.getElementById(id);
  // A link into a hidden article (an example, a record) selects it first.
  choices.forEach(({ select, attribute, sync }) => {
    const owner = node && node.closest("[" + attribute + "]");
    if (owner && select.value !== owner.getAttribute(attribute)) { select.value = owner.getAttribute(attribute); sync(); }
  });
  while (node) {
    if (node.tagName === "DETAILS") node.open = true;
    node = node.parentElement;
  }
}
revealHash();
window.addEventListener("hashchange", revealHash);

document.querySelectorAll(".copy-citation").forEach((button) => {
  button.hidden = false;
  button.addEventListener("click", async () => {
    const status = document.getElementById(button.dataset.status || "copy-status");
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      status.textContent = "Copied the " + (button.dataset.what || "citation") + ".";
    } catch {
      status.textContent = "Select and copy the " + (button.dataset.what || "citation") + ": " + button.dataset.copy;
    }
  });
});

// Evidence stages: each commitment carries its own five notes, written by the build
// from the ledger. Selecting a stage shows that note; nothing is computed here.
function setStage(article, stage) {
  article.querySelectorAll(".stage-notes > [data-stage]").forEach((note) => {
    note.hidden = note.dataset.stage !== stage;
  });
  article.querySelectorAll(".evidence-stages [data-stage]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.stage === stage));
  });
}
document.querySelectorAll(".budget-result").forEach((article) => {
  article.querySelectorAll(".evidence-stages [data-stage]").forEach((b) => {
    b.addEventListener("click", () => setStage(article, b.dataset.stage));
  });
  setStage(article, "announcement");
});
bindChoice("budget-choice", "data-budget", () => {
  document.querySelectorAll(".budget-result").forEach((article) => setStage(article, "announcement"));
});

// Folder-label search: the same words-must-all-match rule as catalog_search, run in
// the browser over docs/data/folders.json. Rendered with text nodes only.
const folderForm = document.getElementById("folder-search");
if (folderForm) {
  const input = document.getElementById("folder-query");
  const status = document.getElementById("folder-status");
  const results = document.getElementById("folder-results");
  const pdf = (bates) => "https://sept11documents.cityofnewyork.us/apps/content/September11_MD/" + bates + ".pdf";
  let index = null;
  const load = () => {
    if (index) return index;
    index = fetch("data/folders.json", { cache: "no-cache" })
      .then((r) => r.ok ? r.json() : Promise.reject(new Error("index unavailable")))
      .then((d) => {
        if (!Array.isArray(d.rows) || !Array.isArray(d.sources)) throw new Error("invalid index");
        return { meta: d, rows: d.rows.map((r) => ({
          source: d.sources[r[0]] || "", box: r[1], folder: r[2], documents: r[3], pages: r[4], first: r[5],
          haystack: ((d.sources[r[0]] || "") + " " + r[1] + " " + r[2]).toLowerCase() })) };
      });
    return index;
  };
  const matches = (row, tokens) => tokens.every((t) => /^\d+$/.test(t)
    ? new RegExp("(^|[^0-9])" + t + "($|[^0-9])").test(row.haystack) : row.haystack.includes(t));
  const item = (row) => {
    const li = document.createElement("li");
    const b = document.createElement("b");
    b.textContent = row.folder || "(no folder label)";
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = " " + [row.source, row.box, row.documents + (row.documents === 1 ? " document" : " documents"),
      row.pages + " pages"].filter(Boolean).join(" · ") + " ";
    const a = document.createElement("a");
    a.className = "cite";
    a.href = pdf(row.first);
    a.textContent = row.first;
    li.append(b, meta, a);
    return li;
  };
  const search = async () => {
    const tokens = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean).slice(0, 8);
    if (!tokens.length) { results.hidden = true; results.replaceChildren(); return; }
    status.textContent = "Searching the saved capture…";
    try {
      const { meta, rows } = await load();
      const hits = rows.filter((r) => matches(r, tokens)).sort((x, y) => y.documents - x.documents);
      const shown = hits.slice(0, 25);
      results.replaceChildren(...shown.map(item));
      results.hidden = !shown.length;
      const docs = hits.reduce((n, r) => n + r.documents, 0);
      status.textContent = hits.length
        ? hits.length + " folder rows (" + docs.toLocaleString("en-US") + " documents) match in the capture of "
          + String(meta.captured_at).slice(0, 10) + (hits.length > shown.length ? "; showing the " + shown.length + " largest." : ".")
        : "No folder label in the capture of " + String(meta.captured_at).slice(0, 10)
          + " contains every word. Labels are the City's own; try one word, or a street name without the number.";
    } catch {
      status.textContent = "The folder index could not be read. Open data/folders.json directly.";
      results.hidden = true;
    }
  };
  folderForm.addEventListener("submit", (event) => { event.preventDefault(); search(); });
  input.addEventListener("input", () => { if (!input.value.trim()) search(); });
}

const watchdogPanel = document.getElementById("watchdog-live");
if (watchdogPanel) {
  const cell = (figure, caption) => {
    const wrap = document.createElement("div");
    const b = document.createElement("b");
    b.textContent = figure;
    const span = document.createElement("span");
    span.textContent = caption;
    wrap.append(b, span);
    return wrap;
  };
  fetch("data/watchdog/latest.json", { cache: "no-cache" })
    .then((r) => r.ok ? r.json() : Promise.reject(new Error("capture unavailable")))
    .then((d) => {
      // Unknown totals never become NaN or a fabricated zero.
      if (!Number.isSafeInteger(d.documents) || d.documents < 1 ||
          !Number.isSafeInteger(d.pages) || d.pages < 1 ||
          typeof d.captured_at !== "string" || !Number.isFinite(Date.parse(d.captured_at))) {
        throw new Error("invalid capture");
      }
      const cells = [
        cell(d.documents.toLocaleString("en-US"), "documents captured"),
        cell(d.pages.toLocaleString("en-US"), "pages in that capture"),
        cell(d.captured_at.slice(0, 10), "capture date (UTC)"),
      ];
      if (d.interval) {
        for (const [key, label] of [["observed_added", "observed added"], ["observed_absent", "observed absent"]]) {
          const n = d.counts?.[key];
          cells.push(cell(Number.isSafeInteger(n) && n >= 0 ? String(n) : "Unknown", label));
        }
      }
      watchdogPanel.replaceChildren(...cells);
      const note = document.getElementById("watchdog-note");
      if (note && !d.interval) {
        note.textContent = "Baseline capture; nothing earlier to compare.";
      } else if (note) {
        note.textContent = "Compared " + String(d.interval.from || "unknown").slice(0, 10) + " to " +
          String(d.interval.to || "unknown").slice(0, 10) + ".";
      }
    })
    .catch(() => {
      watchdogPanel.replaceChildren(cell("Unavailable", "saved capture missing or unreadable"));
    });
}

let printState = [];
window.addEventListener("beforeprint", () => {
  printState = Array.from(document.querySelectorAll(
    ".demo-panel, [data-record], [data-help], [data-budget], [data-example], [data-path], [data-release], [data-collection], [data-address], [data-cite], .statement-timeline > li, .stage-notes > [data-stage], details.fold"))
    .map((el) => ({ el, hidden: el.hidden, open: el.open }));
  printState.forEach(({ el }) => { el.hidden = false; if (el.tagName === "DETAILS") el.open = true; });
});
window.addEventListener("afterprint", () => {
  printState.forEach(({ el, hidden, open }) => { el.hidden = hidden; if (el.tagName === "DETAILS") el.open = open; });
});
