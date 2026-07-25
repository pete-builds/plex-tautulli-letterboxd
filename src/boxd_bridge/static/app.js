const $ = (id) => document.getElementById(id);

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

const isoDaysAgo = (days) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
};

function selectedUser() {
  const picker = $("user");
  return picker && picker.value ? picker.value : null;
}

/** The active `since` value, or null for all time. */
function selectedSince() {
  const range = $("range");
  if (!range || range.value === "all") return null;
  if (range.value === "custom") return $("since").value || null;
  return isoDaysAgo(Number(range.value));
}

function buildUrl(path, { since } = {}) {
  const url = new URL(path, window.location.origin);
  const user = selectedUser();
  if (user) url.searchParams.set("user_id", user);
  const cutoff = since === undefined ? selectedSince() : since;
  if (cutoff) url.searchParams.set("since", cutoff);
  return url;
}

function syncDownloadLink() {
  const dl = $("download");
  if (dl) dl.href = buildUrl("/api/export.csv").toString();
}

async function loadUsers() {
  const picker = $("user");
  if (!picker) return;
  try {
    const res = await fetch("/api/users");
    if (!res.ok) return;
    const { users } = await res.json();
    for (const u of users ?? []) {
      const opt = document.createElement("option");
      opt.value = u.user_id;
      opt.textContent = u.friendly_name || u.username || u.user_id;
      picker.append(opt);
    }
  } catch {
    /* the picker is a convenience; "All users" still works without it */
  }
}

function renderPreview(data) {
  const rows = (data.sample ?? [])
    .map(
      (r) => `<tr>
        <td class="num">${escapeHtml(r.WatchedDate)}</td>
        <td>${escapeHtml(r.Title)}</td>
        <td class="num">${escapeHtml(r.Year ?? "")}</td>
        <td class="num">${escapeHtml(r.tmdbID ?? r.imdbID ?? "-")}</td>
        <td>${r.Rewatch ? "rewatch" : "first"}</td>
      </tr>`
    )
    .join("");

  const scope = data.since
    ? `since ${escapeHtml(data.since)} &middot; ${data.filtered_out} older ${
        data.filtered_out === 1 ? "entry" : "entries"
      } excluded`
    : "all time";

  const empty =
    data.rows === 0
      ? `<p class="note">Nothing to import in this window. The CSV would contain
         only its header row.</p>`
      : "";

  // We store nothing server-side, so the only place "where I left off" can live
  // is a URL the user keeps.
  const nextUrl = buildUrl("/api/export.csv", { since: data.next_since }).toString();

  return `
    <div class="stats">
      <div class="stat"><b>${data.rows}</b><span>entries to import</span></div>
      <div class="stat"><b>${data.rewatches}</b><span>rewatches</span></div>
      <div class="stat"><b>${data.exact_id_matches}</b><span>exact id matches</span></div>
      <div class="stat"><b>${data.parts}</b><span>csv part${data.parts === 1 ? "" : "s"}</span></div>
    </div>
    <p class="note">Scope: ${scope}. Of ${data.total_rows} total diary entries.</p>
    ${empty}
    <div class="scroll"><table>
      <thead><tr><th>Watched</th><th>Title</th><th>Year</th><th>ID</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <div class="next">
      <h3>Next time, import only what is new</h3>
      <p class="note">Bookmark this link. It exports everything watched from
         today onward, so your next review screen stays short.</p>
      <input class="urlbox" type="text" readonly value="${escapeHtml(nextUrl)}"
             onclick="this.select()">
    </div>`;
}

async function preview() {
  const btn = $("preview-btn");
  const out = $("result");
  btn.disabled = true;
  out.hidden = false;
  out.textContent = "Reading watch history…";
  try {
    const res = await fetch(buildUrl("/api/preview"));
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    out.innerHTML = renderPreview(data);
    syncDownloadLink();
  } catch (err) {
    out.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
  } finally {
    btn.disabled = false;
  }
}

$("preview-btn")?.addEventListener("click", preview);
$("user")?.addEventListener("change", syncDownloadLink);
$("since")?.addEventListener("change", syncDownloadLink);
$("range")?.addEventListener("change", (event) => {
  $("since-field").hidden = event.target.value !== "custom";
  syncDownloadLink();
});

// Honour a bookmarked ?since= so a saved incremental link opens pre-filled.
const bookmarked = new URLSearchParams(window.location.search).get("since");
if (bookmarked && $("range")) {
  $("range").value = "custom";
  $("since-field").hidden = false;
  $("since").value = bookmarked;
}

syncDownloadLink();
loadUsers();
