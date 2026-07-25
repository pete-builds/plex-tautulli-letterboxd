const $ = (id) => document.getElementById(id);

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

function selectedUser() {
  const picker = $("user");
  return picker && picker.value ? picker.value : null;
}

/**
 * The active `since` value, or null for all time.
 *
 * There is exactly one source of truth: the select. Preset options carry the
 * resolved date as their value (computed server-side in the export timezone),
 * so nothing here recomputes or caches a second copy that could drift.
 */
function selectedSince() {
  const range = $("range");
  if (!range || range.value === "") return null;   // All time
  if (range.value === "custom") return $("since")?.value || null;
  return range.value;                              // already an ISO date
}

function buildUrl(path, { since } = {}) {
  const url = new URL(path, window.location.origin);
  const user = selectedUser();
  if (user) url.searchParams.set("user_id", user);
  const cutoff = since === undefined ? selectedSince() : since;
  if (cutoff) url.searchParams.set("since", cutoff);
  return url;
}

// Show what will actually happen. Previously the UI could display one window
// while a different one went on the wire; this makes that visible immediately.
function updateResolved() {
  const el = $("resolved");
  if (!el) return;
  const range = $("range");
  const since = selectedSince();
  if (range && range.value === "custom" && !since) {
    el.textContent = "Pick a date to export from.";
  } else if (since) {
    el.textContent = `Exporting watches since ${since}.`;
  } else {
    el.textContent = "Exporting your entire history.";
  }
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

  // "Specific date" with nothing entered used to fall through to an all-time
  // export, which silently ignores what the control says.
  if ($("range")?.value === "custom" && !selectedSince()) {
    out.hidden = false;
    out.innerHTML =
      '<p class="note">Pick a date to export from, or choose a preset.</p>';
    return;
  }

  btn.disabled = true;
  out.hidden = false;
  out.textContent = "Reading watch history…";
  try {
    const res = await fetch(buildUrl("/api/preview"));
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    out.innerHTML = renderPreview(data);
    updateResolved();
  } catch (err) {
    out.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
  } finally {
    btn.disabled = false;
  }
}

$("preview-btn")?.addEventListener("click", preview);
$("user")?.addEventListener("change", updateResolved);
// Both events: `input` fires as the value is committed on mobile, `change` on
// desktop commit and on blur.
$("since")?.addEventListener("input", updateResolved);
$("since")?.addEventListener("change", updateResolved);

$("range")?.addEventListener("change", (event) => {
  // The date input keeps its own value natively while hidden, so there is no
  // second copy to synchronise.
  $("since-field").hidden = event.target.value !== "custom";
  updateResolved();
});

// Build the download URL at click time. A pre-computed href goes stale the
// moment the selection changes without a handler having run, which is how a
// 90-day window was downloaded while the control read "Last year".
$("download")?.addEventListener("click", (event) => {
  event.preventDefault();
  window.location.href = buildUrl("/api/export.csv").toString();
});

// Honour a bookmarked ?since= so a saved incremental link opens pre-filled.
const bookmarked = new URLSearchParams(window.location.search).get("since");
if (bookmarked && $("range") && $("since")) {
  $("range").value = "custom";
  $("since-field").hidden = false;
  $("since").value = bookmarked;
}

updateResolved();
loadUsers();
