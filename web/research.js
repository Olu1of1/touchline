"use strict";

/* Touchline Research: question in, verified answer out, with the evidence (chart, table, tool calls). */

const $ = (id) => document.getElementById(id);
const convo = [];
const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500"];   // validated against the navy surface (dark mode)
const POS = "#3987e5", NEG = "#d95926", SINGLE = "#FFD83D";
const SVGNS = "http://www.w3.org/2000/svg";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const fmt = (v) => (typeof v === "number" ? String(Number(v.toFixed(2))) : String(v));

/* ---------------------------------------------------------------- asking */
async function ask(question) {
  question = question.trim();
  if (!question) return;
  $("empty").hidden = true;
  const li = document.createElement("li");
  li.className = "turn";
  li.innerHTML = `<p class="t-q">${esc(question)}</p><p class="t-a pending">Checking the data…</p>`;
  $("turns").appendChild(li);
  li.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  $("r-status").textContent = "Working on it.";
  let r;
  try {
    const res = await fetch("/api/research", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: convo.slice(-3) }),
    });
    r = await res.json();
    if (!res.ok) throw new Error(r.detail || res.status);
  } catch (e) {
    li.querySelector(".t-a").textContent = `Couldn't get an answer (${e.message}). Try again in a moment.`;
    li.querySelector(".t-a").classList.remove("pending");
    return;
  }
  convo.push({ q: question, a: r.answer });
  renderTurn(li, question, r);
  $("r-status").textContent = r.answer;
  if ($("speak-answers").checked && "speechSynthesis" in window) {
    speechSynthesis.cancel();
    speechSynthesis.speak(new SpeechSynthesisUtterance(r.answer));
  }
}

function renderTurn(li, q, r) {
  const a = li.querySelector(".t-a");
  a.textContent = r.answer;
  a.classList.remove("pending");
  a.tabIndex = -1;
  for (const c of r.cards || []) li.appendChild(renderCard(c));
  const how = document.createElement("details");
  how.className = "how";
  const verdict = {
    accepted: "Checked and accepted", offline: "Answered offline from the same tools",
    rejected_fallback: "The model's drafts failed the check, so this answer comes straight from the tools",
    error_fallback: "The model was unavailable, so this answer comes straight from the tools",
    skipped_fallback: "Answered from the tools",
  }[r.verdict] || r.verdict;
  const retry = r.attempts > 1 && r.problems && r.problems.length === 0
    ? `<li>First draft rejected and corrected. Draft: “${esc(r.drafts[0])}”</li>` : "";
  const rejected = (r.problems || []).length
    ? `<li>The verifier rejected: ${esc(r.problems.join("; "))}${r.drafts.length ? `. Last draft: “${esc(r.drafts[r.drafts.length - 1])}”` : ""}</li>` : "";
  how.innerHTML = `<summary>How this was answered</summary><ul>
    <li>${esc(verdict)}${r.attempts > 1 ? `, after ${r.attempts} attempts` : ""}.</li>${retry}${rejected}
    <li>Tools called: ${esc((r.tools_used || []).join(", ") || "none")}</li>
    <li>Answered by: ${esc(r.backend)} in ${r.latency_s} s</li></ul>`;
  li.appendChild(how);
  a.focus({ preventScroll: true });
}

/* ------------------------------------------------------------------ cards */
function renderCard(c) {
  const fig = document.createElement("figure");
  fig.className = "card";
  const cap = `<figcaption>${esc(c.title)}</figcaption>`;
  let chartEl = null;
  if (c.chart && c.rows.length > 1) chartEl = drawChart(c);
  fig.innerHTML = cap;
  if (chartEl) fig.appendChild(chartEl);
  if (c.note) { const n = document.createElement("p"); n.className = "note"; n.textContent = c.note; fig.appendChild(n); }
  const wrap = document.createElement("div");
  wrap.className = "tablewrap";
  wrap.tabIndex = 0;
  wrap.setAttribute("role", "region");
  wrap.setAttribute("aria-label", `${c.title}, table`);
  const head = c.columns.map((h) => `<th scope="col">${esc(h)}</th>`).join("");
  const body = c.rows.map((r) => `<tr>${r.map((v, i) => i === 0 ? `<th scope="row">${esc(fmt(v))}</th>` : `<td class="${typeof v === "number" ? "num" : ""}">${esc(fmt(v))}</td>`).join("")}</tr>`).join("");
  wrap.innerHTML = `<table><caption class="visually-hidden">${esc(c.title)}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  fig.appendChild(wrap);
  return fig;
}

function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

function showTip(evt, html) {
  const t = $("tip");
  t.innerHTML = html;
  t.hidden = false;
  const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
  if (y + h > innerHeight - 8) y = evt.clientY - h - pad;
  t.style.left = `${x}px`; t.style.top = `${y}px`;
}
const hideTip = () => { $("tip").hidden = true; };

function drawChart(c) {
  const xi = c.columns.indexOf(c.chart.x);
  if (c.chart.type === "line") return lineChart(c, xi);
  const yi = c.columns.indexOf(c.chart.y);
  if (xi < 0 || yi < 0) return null;
  const vertical = c.columns[xi] === "MW";
  return vertical ? columnChart(c, xi, yi) : barChart(c, xi, yi, c.chart.type === "diverging");
}

/* horizontal bars: rankings with names */
function barChart(c, xi, yi, diverging) {
  const rows = c.rows.slice(0, 20);
  const rowH = 26, gap = 6, left = 150, right = 70, top = 8;
  const W = 760, H = top + rows.length * (rowH + gap) + 10;
  const vals = rows.map((r) => Number(r[yi]) || 0);
  const max = Math.max(...vals.map(Math.abs), 1e-9);
  const min = diverging ? -max : 0;
  const plotW = W - left - right;
  const sx = (v) => left + ((v - min) / ((diverging ? max : max) - min)) * plotW;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", "aria-hidden": "true" });
  const zero = sx(0);
  el("line", { x1: zero, x2: zero, y1: 0, y2: H - 6, class: "axis" }, svg);
  rows.forEach((r, i) => {
    const v = vals[i];
    const y = top + i * (rowH + gap);
    const x0 = Math.min(zero, sx(v)), w = Math.max(2, Math.abs(sx(v) - zero));
    const colour = diverging ? (v >= 0 ? POS : NEG) : SINGLE;
    const lab = el("text", { x: left - 10, y: y + rowH / 2, class: "lab", "text-anchor": "end", "dominant-baseline": "middle" }, svg);
    lab.textContent = String(r[xi]).length > 18 ? String(r[xi]).slice(0, 17) + "…" : String(r[xi]);
    const bar = el("rect", { x: x0, y, width: w, height: rowH, rx: 4, fill: colour }, svg);
    if (i < 3 || diverging && i >= rows.length - 2) {
      // negative values sit just right of the zero line, so they never collide with the row labels
      const vt = el("text", { x: v >= 0 ? x0 + w + 6 : zero + 6, y: y + rowH / 2, class: "val",
        "text-anchor": "start", "dominant-baseline": "middle" }, svg);
      vt.textContent = diverging ? (v > 0 ? "+" : "") + fmt(v) : fmt(v);
    }
    const hit = el("rect", { x: left - 140, y: y - gap / 2, width: W - left + 140, height: rowH + gap, fill: "transparent" }, svg);
    const tip = `<strong>${esc(r[xi])}</strong><br>${esc(c.columns[yi])}: ${esc((diverging && v > 0 ? "+" : "") + fmt(v))}`;
    hit.addEventListener("mousemove", (e) => { bar.setAttribute("opacity", "0.8"); showTip(e, tip); });
    hit.addEventListener("mouseleave", () => { bar.removeAttribute("opacity"); hideTip(); });
  });
  return svg;
}

/* vertical columns: a sequence such as matchweeks */
function columnChart(c, xi, yi) {
  const rows = c.rows;
  const W = 760, H = 240, left = 40, right = 10, top = 14, bottom = 28;
  const vals = rows.map((r) => Number(r[yi]) || 0);
  const max = Math.max(...vals, 1e-9);
  const step = (W - left - right) / rows.length;
  const bw = Math.max(3, step - 2);
  const sy = (v) => top + (1 - v / max) * (H - top - bottom);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", "aria-hidden": "true" });
  [0, max / 2, max].forEach((g) => {
    el("line", { x1: left, x2: W - right, y1: sy(g), y2: sy(g), class: "grid" }, svg);
    const t = el("text", { x: left - 6, y: sy(g), class: "tick", "text-anchor": "end", "dominant-baseline": "middle" }, svg);
    t.textContent = fmt(Math.round(g * 100) / 100);
  });
  rows.forEach((r, i) => {
    const x = left + i * step + 1, y = sy(vals[i]);
    const bar = el("rect", { x, y, width: bw, height: Math.max(0, H - bottom - y), rx: Math.min(4, bw / 2), fill: SINGLE }, svg);
    if (i % 5 === 0 || i === rows.length - 1) {
      const t = el("text", { x: x + bw / 2, y: H - 10, class: "tick", "text-anchor": "middle" }, svg);
      t.textContent = r[xi];
    }
    const hit = el("rect", { x: left + i * step, y: top, width: step, height: H - top - bottom, fill: "transparent" }, svg);
    const tip = `<strong>${esc(c.columns[xi])} ${esc(r[xi])}</strong>${r[1] ? `, v ${esc(r[1])}` : ""}<br>${esc(c.columns[yi])}: ${esc(fmt(vals[i]))}`;
    hit.addEventListener("mousemove", (e) => { bar.setAttribute("opacity", "0.8"); showTip(e, tip); });
    hit.addEventListener("mouseleave", () => { bar.removeAttribute("opacity"); hideTip(); });
  });
  return svg;
}

/* lines: league position by matchweek (1 at the top), up to 4 clubs */
function lineChart(c, xi) {
  const series = (c.chart.series || []).slice(0, 4);
  const idx = series.map((s) => c.columns.indexOf(s));
  const rows = c.rows;
  const W = 760, H = 300, left = 40, right = 120, top = 14, bottom = 28;
  const n = rows.length;
  const maxPos = Math.max(...rows.flatMap((r) => idx.map((i) => r[i])), 4);
  const sx = (k) => left + (k / Math.max(1, n - 1)) * (W - left - right);
  const sy = (p) => top + ((p - 1) / Math.max(1, maxPos - 1)) * (H - top - bottom);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", "aria-hidden": "true" });
  [1, Math.ceil(maxPos / 2), maxPos].forEach((p) => {
    el("line", { x1: left, x2: W - right, y1: sy(p), y2: sy(p), class: "grid" }, svg);
    const t = el("text", { x: left - 8, y: sy(p), class: "tick", "text-anchor": "end", "dominant-baseline": "middle" }, svg);
    t.textContent = p === 1 ? "1st" : `${p}th`;
  });
  [0, Math.floor((n - 1) / 2), n - 1].forEach((k) => {
    const t = el("text", { x: sx(k), y: H - 8, class: "tick", "text-anchor": "middle" }, svg);
    t.textContent = `MW ${rows[k][xi]}`;
  });
  const ends = [];
  idx.forEach((ci, s) => {
    const d = rows.map((r, k) => `${k ? "L" : "M"}${sx(k).toFixed(1)},${sy(r[ci]).toFixed(1)}`).join("");
    el("path", { d, fill: "none", stroke: SERIES[s], "stroke-width": 2.5, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    ends.push({ y: sy(rows[n - 1][ci]), s, name: series[s].replace(" position", "") });
  });
  ends.sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 16) ends[i].y = ends[i - 1].y + 16;
  ends.forEach((e) => {
    el("circle", { cx: W - right + 8, cy: e.y, r: 4, fill: SERIES[e.s] }, svg);
    const t = el("text", { x: W - right + 18, y: e.y, class: "lab", "dominant-baseline": "middle" }, svg);
    t.textContent = e.name;
  });
  const cross = el("line", { x1: 0, x2: 0, y1: top, y2: H - bottom, class: "cross", visibility: "hidden" }, svg);
  const hit = el("rect", { x: left, y: top, width: W - left - right, height: H - top - bottom, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", (e) => {
    const box = svg.getBoundingClientRect();
    const px = (e.clientX - box.left) * (W / box.width);
    const k = Math.max(0, Math.min(n - 1, Math.round(((px - left) / (W - left - right)) * (n - 1))));
    cross.setAttribute("x1", sx(k)); cross.setAttribute("x2", sx(k)); cross.setAttribute("visibility", "visible");
    const lines = idx.map((ci, s) => `<span class="sw" style="background:${SERIES[s]}"></span>${esc(series[s].replace(" position", ""))}: ${rows[k][ci]}`).join("<br>");
    showTip(e, `<strong>Matchweek ${esc(rows[k][xi])}</strong><br>${lines}`);
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
  return svg;
}

/* ------------------------------------------------------------------ voice */
function setupMic() {
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Rec) return;
  const mic = $("r-mic");
  mic.hidden = false;
  let rec = null;
  mic.addEventListener("click", () => {
    if (rec) { rec.abort(); return; }
    rec = new Rec();
    rec.lang = "en-GB";
    rec.interimResults = true;
    mic.textContent = "Listening…";
    mic.setAttribute("aria-pressed", "true");
    rec.onresult = (e) => {
      const txt = Array.from(e.results).map((x) => x[0].transcript).join(" ");
      $("q").value = txt;
      if (e.results[e.results.length - 1].isFinal) { ask(txt); $("q").value = ""; }
    };
    rec.onend = rec.onerror = () => { rec = null; mic.textContent = "Speak"; mic.setAttribute("aria-pressed", "false"); };
    rec.start();
  });
}

$("r-form").addEventListener("submit", (e) => { e.preventDefault(); const v = $("q").value; $("q").value = ""; ask(v); });
document.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));
setupMic();
const pre = new URLSearchParams(location.search).get("q");
if (pre) ask(pre);
