"use strict";

/* Touchline player.
 * Plays a commentary timeline against a match clock. The server's Director already
 * planned the airtime; this player keeps the same rules live (urgent lines interrupt,
 * routine lines expire) because real voices never take exactly the planned time. */

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

const S = {
  data: null, config: null, mode: "highlights", voice: "browser",
  clock: 0, playing: false, idx: 0, segIdx: 0, ballIdx: 0,
  speaking: null, pending: [], lastLine: null, rate: 1.0, crowdOn: true, pitchShown: false,
  lastTick: null, speed: parseFloat(params.get("speed") || "1"),
  markers: {}, moments: [], roarUntil: 0, askLog: [],
};

const ROUTINE = new Set(["ball", "cross", "take_on", "claim", "long_ball", "restart", "offside", "turnover",
                         "through_ball", "counter", "corner", "free_kick"]);
const MOMENT_KINDS = new Set(["goal", "big_chance", "penalty", "red"]);

function expiry(u) {
  if (ROUTINE.has(u.kind)) return 4;
  if (u.kind.startsWith("colour")) return 25;
  return 12;
}

/* ------------------------------------------------------------------ voices */
const Browser = {
  voice: null, token: 0,
  pickVoice() {
    const vs = speechSynthesis.getVoices();
    // prefer the natural-sounding voices (Edge's "Online (Natural)" ones), British English first
    const en = vs.filter((v) => v.lang && v.lang.startsWith("en"));
    const natural = (v) => /natural|neural|online/i.test(v.name);
    this.voice = en.find((v) => v.lang === "en-GB" && natural(v)) || en.find(natural) ||
                 en.find((v) => v.lang === "en-GB") || en[0] || null;
  },
  speak(item, done) {
    const t = ++this.token;
    const u = new SpeechSynthesisUtterance(item.text);
    if (this.voice) u.voice = this.voice;
    const ex = item.excite || 0;
    u.rate = Math.min(2, S.rate * (ex >= 1.5 ? 1.1 : ex >= 0.8 ? 1.04 : 1));
    u.pitch = 1 + Math.min(0.25, 0.12 * ex);
    u.onend = u.onerror = () => { if (t === this.token) done(); };
    speechSynthesis.speak(u);
  },
  cancel() { this.token++; speechSynthesis.cancel(); },
};

const Azure = {
  cache: new Map(), source: null, token: 0,
  fetch(item) {
    const key = `${item.text}|${item.excite || 0}|${S.rate}`;
    if (!this.cache.has(key)) {
      this.cache.set(key, fetch("/api/tts", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: item.text, excite: item.excite || 0, rate: S.rate }),
      }).then((r) => { if (!r.ok) throw new Error(`voice ${r.status}`); return r.arrayBuffer(); })
        .then((buf) => Audio.ctx.decodeAudioData(buf)));
    }
    return this.cache.get(key);
  },
  speak(item, done) {
    const t = ++this.token;
    this.fetch(item).then((buffer) => {
      if (t !== this.token) return;
      const src = Audio.ctx.createBufferSource();
      src.buffer = buffer;
      const pan = Audio.ctx.createStereoPanner();
      pan.pan.value = item.pan || 0;
      src.connect(pan).connect(Audio.ctx.destination);
      src.onended = () => { if (t === this.token) done(); };
      this.source = src;
      src.start();
    }).catch(() => { if (t === this.token) { Browser.speak(item, done); } });
  },
  cancel() { this.token++; if (this.source) { try { this.source.stop(); } catch (e) { /* already stopped */ } } },
  prefetch() {
    if (!S.data) return;
    const tl = S.data.timeline;
    for (let i = S.idx; i < Math.min(tl.length, S.idx + 6); i++) {
      if (tl[i].start - S.clock < 12) this.fetch(tl[i]).catch(() => {});
    }
  },
};

const Reader = {
  timer: null,
  speak(item, done) {
    const region = (item.interrupt || item.user) ? $("sr-assertive") : $("sr-polite");
    region.textContent = "";
    setTimeout(() => { region.textContent = item.text; }, 30);
    const secs = item.text.split(/\s+/).length / (2.6 * S.rate) + 0.3;
    this.timer = setTimeout(done, secs * 1000);
  },
  cancel() { clearTimeout(this.timer); },
};

function engine() { return S.voice === "azure" ? Azure : S.voice === "screenreader" ? Reader : Browser; }

/* ------------------------------------------------------------------- queue */
function due(item) {
  if (S.speaking) {
    const cur = S.speaking;
    const urgent = item.interrupt && item.priority > cur.priority;
    const soft = cur.kind === "ball" && item.priority >= 55;
    if (urgent || soft || item.user) {
      engine().cancel();
      S.speaking = null;
      startSpeaking(item);
      return;
    }
    if (item.kind === "ball") S.pending = S.pending.filter((p) => p.kind !== "ball");
    S.pending.push(item);
    return;
  }
  startSpeaking(item);
}

function startSpeaking(item) {
  S.speaking = item;
  if (!item.user) S.lastLine = item;
  showCaption(item);
  if (item.kind === "goal") goalMoment();
  engine().speak(item, () => {
    if (S.speaking === item) { S.speaking = null; nextPending(); }
  });
}

function nextPending() {
  S.pending = S.pending.filter((p) => p.user || S.clock - p.start <= expiry(p));
  if (!S.pending.length) return;
  S.pending.sort((a, b) => b.priority - a.priority || a.start - b.start);
  startSpeaking(S.pending.shift());
}

function stopSpeech() {
  engine().cancel();
  S.speaking = null;
  S.pending = [];
}

function say(text, opts = {}) {
  due(Object.assign({ text, kind: "answer", priority: 99, start: S.clock, user: true, excite: 0, pan: 0 }, opts));
}

/* ----------------------------------------------------------------- display */
function showCaption(item) {
  const cap = $("caption");
  if (cap.textContent && !item.user) $("previous").textContent = cap.textContent;
  cap.textContent = item.text;
  cap.dataset.kind = "";
  void cap.offsetWidth;          // restart the goal animation
  cap.dataset.kind = item.kind;
}

function snapAt(t) {
  const sn = S.data.snapshots;
  let lo = 0, hi = sn.length - 1, best = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (sn[mid].t <= t) { best = sn[mid]; lo = mid + 1; } else hi = mid - 1;
  }
  return best;
}

function phase(t) {
  const m = S.markers;
  if (m.kickoff1 === undefined || t < m.kickoff1) return "pre";
  if (m.halfTime !== undefined && t >= m.halfTime && t < m.kickoff2) return "ht";
  if (m.fullTime !== undefined && t >= m.fullTime) return "ft";
  return "live";
}

function clockText(t) {
  const p = phase(t);
  if (p === "pre") return "Before kick-off";
  if (p === "ht") return "Half-time";
  if (p === "ft") return "Full-time";
  const sn = snapAt(t);
  return sn ? sn.clock : "1'";
}

function clockSpoken(t) {
  const p = phase(t);
  if (p === "pre") return "before kick-off";
  if (p === "ht") return "at half-time";
  if (p === "ft") return "at full-time";
  const c = clockText(t).replace("'", "");
  if (c.includes("+")) { const [b, a] = c.split("+"); return `${a} minutes into added time at the end of the ${b === "45" ? "first" : "second"} half`; }
  return `after ${c} minutes`;
}

function updateBoard() {
  const sn = phase(S.clock) === "pre" ? null : snapAt(S.clock);
  const sc = sn ? sn.score : [0, 0];
  $("home-goals").textContent = sc[0];
  $("away-goals").textContent = sc[1];
  $("clock").textContent = clockText(S.clock);
}

/* ------------------------------------------------------------ on-demand */
function teamNames() { return [S.data.match.home.short, S.data.match.away.short]; }

function askScore() {
  if (!S.data) return;
  const [h, a] = teamNames();
  const sn = phase(S.clock) === "pre" ? null : snapAt(S.clock);
  const sc = sn ? sn.score : [0, 0];
  say(`${h} ${sc[0]}, ${a} ${sc[1]}, ${clockSpoken(S.clock)}.`);
}

function askWin() {
  if (!S.data) return;
  const [h, a] = teamNames();
  const sn = snapAt(S.clock) || S.data.snapshots[0];
  const [w, d, l] = sn.wp.map((v) => Math.round(v * 100));
  if (phase(S.clock) === "ft") { askScore(); return; }
  say(`Chances right now: ${h} ${w} percent, the draw ${d}, ${a} ${l}.`);
}

function askStats() {
  if (!S.data) return;
  const [h, a] = teamNames();
  const sn = snapAt(S.clock);
  if (!sn || phase(S.clock) === "pre") { say("The match hasn't started yet."); return; }
  const cards = (c) => {
    const parts = [];
    if (c[0]) parts.push(`${c[0]} yellow`);
    if (c[1]) parts.push(`${c[1]} red`);
    return parts.length ? parts.join(" and ") : "no cards";
  };
  say(`Possession: ${h} ${sn.possession_pct[0]} percent, ${a} ${sn.possession_pct[1]}. ` +
      `Shots: ${h} ${sn.shots[0]}, ${a} ${sn.shots[1]}, with ${sn.on_target[0]} and ${sn.on_target[1]} on target. ` +
      `Cards: ${h} ${cards(sn.cards[0])}, ${a} ${cards(sn.cards[1])}.`);
}

function askBall() {
  if (!S.data) return;
  const p = phase(S.clock);
  if (p !== "live") { say(`No ball in play, it's ${clockText(S.clock).toLowerCase()}.`); return; }
  const [h, a] = teamNames();
  const pt = ballAt(S.clock);
  if (!pt) { say("Waiting for kick-off."); return; }
  const [, x, y, team] = pt;
  const who = team === 0 ? h : a;
  let where;
  if (x < 17) where = `close to ${h}'s goal, on your left`;
  else if (x < 42) where = `in ${h}'s half, on your left`;
  else if (x < 63) where = "around halfway";
  else if (x < 88) where = `in ${a}'s half, on your right`;
  else where = `close to ${a}'s goal, on your right`;
  const wide = y < 14 || y > 54 ? ", out near a touchline" : "";
  say(`${who} have the ball, ${where}${wide}.`);
}

function repeatLast() { if (S.lastLine) say(S.lastLine.text, { excite: S.lastLine.excite }); }

function help() {
  say("Space to play or pause. A to ask a question. S for the score. W for who is likely to win. P for stats. L for where the ball is. " +
      "R to repeat. N and B for the next or previous key moment. Plus and minus change the speaking speed. " +
      "C turns the crowd sound on or off.");
}

/* ------------------------------------------------------------------ audio */
const Audio = {
  ctx: null, gain: null, pan: null,
  init() {
    if (this.ctx) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    this.ctx = new AC();
    // a soft, synthesised crowd: filtered noise that swells with danger and roars for goals
    const len = this.ctx.sampleRate * 4;
    const buf = this.ctx.createBuffer(2, len, this.ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      let b0 = 0, b1 = 0, b2 = 0;
      for (let i = 0; i < len; i++) {
        const w = Math.random() * 2 - 1;
        b0 = 0.99765 * b0 + w * 0.0990460; b1 = 0.96300 * b1 + w * 0.2965164; b2 = 0.57000 * b2 + w * 1.0526913;
        d[i] = (b0 + b1 + b2 + w * 0.1848) * 0.12;
      }
    }
    const src = this.ctx.createBufferSource();
    src.buffer = buf; src.loop = true;
    const lp = this.ctx.createBiquadFilter();
    lp.type = "lowpass"; lp.frequency.value = 1100;
    this.gain = this.ctx.createGain(); this.gain.gain.value = 0;
    this.pan = this.ctx.createStereoPanner();
    src.connect(lp).connect(this.gain).connect(this.pan).connect(this.ctx.destination);
    src.start();
  },
  update(danger, pan) {
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    let g = 0;
    if (S.crowdOn && S.playing) {
      g = 0.02 + 0.10 * danger;
      const left = (S.roarUntil - performance.now()) / 1000;
      if (left > 0) g = Math.max(g, 0.06 + 0.30 * Math.min(1, left / 5));
      if (phase(S.clock) !== "live") g = Math.min(g, 0.025);
    }
    this.gain.gain.setTargetAtTime(g, now, 0.35);
    this.pan.pan.setTargetAtTime(pan * 0.7, now, 0.5);
  },
};

function goalMoment() {
  S.roarUntil = performance.now() + 6000;
  if (navigator.vibrate && S.voice !== "screenreader") navigator.vibrate([180, 90, 180, 90, 420]);
}

function ballAt(t) {
  const b = S.data.ball;
  if (!b.length || t < b[0][0]) return null;
  while (S.ballIdx + 1 < b.length && b[S.ballIdx + 1][0] <= t) S.ballIdx++;
  while (S.ballIdx > 0 && b[S.ballIdx][0] > t) S.ballIdx--;
  return b[S.ballIdx];
}

function ballInterp(t) {
  const b = S.data.ball;
  const p = ballAt(t);
  if (!p) return null;
  const n = b[S.ballIdx + 1];
  if (!n || n[0] - p[0] > 8) return p;
  const f = Math.min(1, (t - p[0]) / Math.max(0.2, n[0] - p[0]));
  return [t, p[1] + (n[1] - p[1]) * f, p[2] + (n[2] - p[2]) * f, p[3]];
}

function dangerOf(pt) {
  if (!pt) return 0;
  const ax = pt[3] === 0 ? pt[1] : 105 - pt[1];
  const d = Math.max(0, (ax - 60) / 45);
  return Math.min(1, d * d * 1.2);
}

/* ------------------------------------------------------------------ pitch */
function drawPitch() {
  if (!S.pitchShown || !S.data) return;
  const c = $("pitch"), g = c.getContext("2d"), k = 10;
  g.clearRect(0, 0, c.width, c.height);
  for (let i = 0; i < 12; i++) {
    g.fillStyle = i % 2 ? "#10422F" : "#0E3A2B";
    g.fillRect(i * c.width / 12, 0, c.width / 12 + 1, c.height);
  }
  g.strokeStyle = "rgba(244,247,251,.75)"; g.lineWidth = 3;
  g.strokeRect(1.5, 1.5, c.width - 3, c.height - 3);
  g.beginPath(); g.moveTo(52.5 * k, 0); g.lineTo(52.5 * k, 68 * k); g.stroke();
  g.beginPath(); g.arc(52.5 * k, 34 * k, 9.15 * k, 0, Math.PI * 2); g.stroke();
  for (const side of [0, 1]) {
    const x0 = side ? 105 - 16.5 : 0, x1 = side ? 105 - 5.5 : 0;
    g.strokeRect(x0 * k, 13.84 * k, 16.5 * k, 40.32 * k);
    g.strokeRect(x1 * k, 24.84 * k, 5.5 * k, 18.32 * k);
  }
  const [h, a] = teamNames();
  g.font = "700 26px 'Atkinson Hyperlegible', sans-serif";
  g.textBaseline = "middle";
  dot(g, 36, 40, 11, 0); g.fillStyle = "rgba(244,247,251,.95)"; g.textAlign = "left"; g.fillText(`${h} attack this way →`, 56, 41);
  dot(g, c.width - 36, c.height - 38, 11, 1); g.fillStyle = "rgba(244,247,251,.95)"; g.textAlign = "right"; g.fillText(`← ${a} attack this way`, c.width - 56, c.height - 37);
  const pt = ballInterp(S.clock);
  const live = pt && phase(S.clock) === "live";
  if (S.squad) {
    g.font = "700 17px 'Atkinson Hyperlegible', sans-serif"; g.textAlign = "center";
    for (const p of smoothPositions(S.clock, live ? pt : null)) {
      dot(g, p.x * k, (68 - p.y) * k, 17, p.team);
      g.fillStyle = p.team === 0 ? "#0A1838" : "#F4F7FB";
      g.fillText(String(p.num), p.x * k, (68 - p.y) * k + 1);
    }
  }
  if (live) {
    g.beginPath(); g.arc(pt[1] * k, (68 - pt[2]) * k, 9, 0, Math.PI * 2);
    g.fillStyle = "#FFD83D"; g.fill();
    g.lineWidth = 3; g.strokeStyle = "#0A1838"; g.stroke();
  }
}

/* Home: chalk shirts with navy numbers. Away: navy shirts with a chalk ring. */
function dot(g, x, y, r, team) {
  g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2);
  g.fillStyle = team === 0 ? "#F4F7FB" : "#0A1838"; g.fill();
  g.lineWidth = 3; g.strokeStyle = team === 0 ? "#0A1838" : "#F4F7FB"; g.stroke();
}

/* Estimated positions. The data records touches, not tracking: each player holds a spot set by their
 * role, the whole team shifts with the ball (further up when it has the ball), and a player is pulled
 * to the exact place whenever the data says they touched it. Coordinates are the listener frame. */
const ROLE_SPOT = {
  GK: [5, 34], RB: [30, 10], LB: [30, 58], RWB: [40, 8], LWB: [40, 60], RCB: [25, 22], CB: [24, 34], LCB: [25, 46],
  DM: [38, 34], RDM: [38, 26], LDM: [38, 42], RCM: [48, 22], LCM: [48, 46], RM: [55, 10], LM: [55, 58],
  RAM: [62, 22], CAM: [62, 34], LAM: [62, 46], RW: [68, 10], LW: [68, 58], ST: [76, 34], RST: [74, 26], LST: [74, 42],
};
const clampN = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/* Players run, they don't teleport: cap how far each shirt moves per second of match time.
 * A jump in the clock (seeking, next moment) snaps everyone to place. */
function smoothPositions(t, ball) {
  const target = playerPositions(t, ball);
  const dt = t - (S.posClock ?? -99);
  const prev = S.posPrev || {};
  const snap = dt < 0 || dt > 3;
  const next = {};
  const out = target.map((p) => {
    const key = `${p.team}-${p.num}`, q = prev[key];
    let x = p.x, y = p.y;
    if (q && !snap) {
      const d = Math.hypot(p.x - q.x, p.y - q.y), max = (p.onBall ? 14 : 8.5) * dt;
      if (d > max) { x = q.x + (p.x - q.x) * max / d; y = q.y + (p.y - q.y) * max / d; }
    }
    next[key] = { x, y };
    return { ...p, x, y };
  });
  S.posPrev = next; S.posClock = t;
  return out;
}

function playerPositions(t, ball) {
  const sq = S.squad, T = sq.touches;
  // the latest touch at or before t
  let i = Math.min(S.touchIdx, T.length - 1);
  while (i + 1 < T.length && T[i + 1][0] <= t) i++;
  while (i > 0 && T[i][0] > t) i--;
  S.touchIdx = i;
  const cur = T[i] && T[i][0] <= t ? T[i] : null;
  const next = T[i + 1] && T[i + 1][0] > t ? T[i + 1] : (cur ? null : T[0]);
  const bx = ball ? ball[1] : 52.5, by = ball ? ball[2] : 34;
  const poss = ball ? ball[3] : -1;
  const out = [];
  sq.players.forEach((p, idx) => {
    if (t < p.on || t >= p.off) return;
    const spot = ROLE_SPOT[p.role] || [50, 34];
    // work in this team's attacking frame, then flip the away side into the listener frame
    const home = p.team === 0;
    const ax = home ? bx : 105 - bx, ay = home ? by : 68 - by;
    const inPoss = poss === p.team;
    let x, y;
    if (p.role === "GK") {
      x = 4 + Math.max(0, ax - 30) * 0.12; y = 34 + (ay - 34) * 0.15;
    } else {
      x = clampN(spot[0] + (ax - 52.5) * 0.6 + (inPoss ? 6 : -4), 9, 100);
      y = clampN(34 + (spot[1] - 34) * (inPoss ? 1.05 : 0.8) + (ay - 34) * 0.3, 3, 65);
    }
    if (!home) { x = 105 - x; y = 68 - y; }
    // pull towards real touches: the player on the ball, the next one to receive it, the last one to release it
    let w = 0, tx = x, ty = y;
    if (cur && cur[1] === idx && (!next || t < next[0])) {
      w = 1;
      const follows = cur[4] !== cur[2] || cur[5] !== cur[3];
      tx = follows && ball ? bx : cur[2]; ty = follows && ball ? by : cur[3];
    } else if (next && next[1] === idx) {
      w = clampN(1 - (next[0] - t) / 2.5, 0, 1); tx = next[2]; ty = next[3];
    } else if (cur && cur[1] === idx) {
      w = 1; tx = cur[4]; ty = cur[5];
    } else {
      // recently released the ball: drift back to shape over two seconds
      for (let j = i; j >= 0 && t - T[j][0] < 4; j--) {
        if (T[j][1] === idx) {
          const until = T[j + 1] ? T[j + 1][0] : T[j][0];
          w = clampN(1 - (t - until) / 2, 0, 1); tx = T[j][4]; ty = T[j][5];
          break;
        }
      }
    }
    out.push({ x: x + (tx - x) * w, y: y + (ty - y) * w, team: p.team, num: p.num, onBall: w > 0.5 });
  });
  return out;
}

function togglePitch() {
  S.pitchShown = !S.pitchShown;
  $("pitch-frame").dataset.shown = String(S.pitchShown);
  drawPitch();
}

/* --------------------------------------------------------------- playback */
function seek(t) {
  stopSpeech();
  S.clock = t;
  const tl = S.data.timeline;
  let i = 0;
  while (i < tl.length && tl[i].start < t) i++;
  S.idx = i;
  S.ballIdx = 0;
  updateBoard();
}

function jumpToSegment(i) {
  const segs = S.data.segments;
  if (i >= segs.length) { finish(); return; }
  S.segIdx = i;
  seek(segs[i].start);
  if (segs[i].bridge) say(segs[i].bridge, { kind: "bridge", priority: 98, user: false });
}

function finish() {
  setPlaying(false);
  say(S.mode === "highlights" ? "That's the end of the key moments. Press Play to hear them again." : "That's the end of the match.");
  S.finished = true;
}

function setPlaying(on) {
  if (!S.data) return;
  S.playing = on;
  const b = $("play");
  b.textContent = on ? "Pause" : "Play";
  b.setAttribute("aria-pressed", String(on));
  if (!on) { stopSpeech(); Audio.update(0, 0); }
}

function togglePlay() {
  if (!S.data) return;
  Audio.init();
  if (Audio.ctx && Audio.ctx.state === "suspended") Audio.ctx.resume();
  if (S.finished) { S.finished = false; S.mode === "highlights" ? jumpToSegment(0) : seek(0); }
  setPlaying(!S.playing);
}

function moment(dir) {
  if (!S.data || !S.moments.length) return;
  const t = S.clock;
  let target;
  if (dir > 0) target = S.moments.find((m) => m.start - 12 > t + 1);
  else target = [...S.moments].reverse().find((m) => m.start - 12 < t - 15);
  if (!target) { say(dir > 0 ? "No more key moments." : "This is the first key moment."); return; }
  const at = Math.max(0, target.start - 12);
  if (S.mode === "highlights") {
    const i = S.data.segments.findIndex((s) => s.start <= at + 0.5 && s.end >= at);
    if (i >= 0) S.segIdx = i;
  }
  seek(at);
  if (!S.playing) setPlaying(true);
}

function tick() {
  const now = performance.now();
  const dt = S.lastTick ? Math.min(0.5, (now - S.lastTick) / 1000) : 0;
  S.lastTick = now;
  if (!S.data) return;
  if (S.playing) {
    S.clock += dt * S.speed;
    if (S.mode === "highlights") {
      const seg = S.data.segments[S.segIdx];
      if (seg && S.clock >= seg.end) { jumpToSegment(S.segIdx + 1); return; }
    }
    const tl = S.data.timeline;
    while (S.idx < tl.length && tl[S.idx].start <= S.clock) {
      const u = tl[S.idx++];
      if (S.clock - u.start <= expiry(u)) due(u);
    }
    if (S.voice === "azure") Azure.prefetch();
    if (S.idx >= tl.length && S.clock > tl[tl.length - 1].end + 4 && !S.speaking) finish();
    updateBoard();
  }
  const pt = S.playing ? ballInterp(S.clock) : null;
  Audio.update(dangerOf(pt), pt ? (pt[1] - 52.5) / 52.5 : 0);
  drawPitch();
}

/* -------------------------------------------------------------------- ask */
const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;

async function askQuestion(q) {
  q = (q || "").trim();
  if (!S.data || !q) return;
  $("previous").textContent = `You asked: ${q}`;
  say("One moment.", { kind: "answer", priority: 90 });
  const started = performance.now();
  let r;
  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ match_id: S.data.match.match_id, t: S.clock, question: q }),
    });
    r = await res.json();
    if (!res.ok) throw new Error(r.detail || res.status);
  } catch (e) {
    say("Sorry, I couldn't get an answer just now. Try again in a moment.");
    return;
  }
  say(r.answer);
  S.askLog.unshift({ q, at: clockText(S.clock), ms: Math.round(performance.now() - started), ...r });
  renderAskLog();
}

function listen() {
  if (!Rec) { $("ask-input").focus(); say("Type your question, then press Enter."); return; }
  if (recognizer) { recognizer.abort(); return; }
  stopSpeech();
  recognizer = new Rec();
  recognizer.lang = "en-GB";
  recognizer.interimResults = true;
  recognizer.maxAlternatives = 1;
  const mic = $("ask-mic");
  mic.setAttribute("aria-pressed", "true");
  mic.textContent = "Listening…";
  $("ask-hint").textContent = "Listening. Ask your question.";
  recognizer.onresult = (e) => {
    const txt = Array.from(e.results).map((x) => x[0].transcript).join(" ");
    $("ask-input").value = txt;
    if (e.results[e.results.length - 1].isFinal) askQuestion(txt);
  };
  const done = () => {
    recognizer = null;
    mic.setAttribute("aria-pressed", "false");
    mic.textContent = "Speak";
    $("ask-hint").textContent = "Press A to ask by voice or type a question. Answers only use what has happened so far.";
  };
  recognizer.onend = done;
  recognizer.onerror = (e) => { done(); if (e.error === "not-allowed") say("I need permission to use the microphone. You can type instead."); };
  recognizer.start();
}

function renderAskLog() {
  const el = $("ask-log");
  if (!el) return;
  const esc = (x) => String(x).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
  el.innerHTML = S.askLog.length ? S.askLog.map((a) => `<tr><td>${esc(a.at)}</td><td>${esc(a.q)}</td>
    <td>${esc(a.answer)}${a.attempts > 1 ? `<br><span class="verdict-rejected">first draft rejected: ${esc((a.problems || []).join("; ") || "unsupported claim")}</span>` : ""}</td>
    <td>${esc((a.tools_used || []).join(", "))}</td><td>${esc(a.verdict)}, ${a.ms} ms</td></tr>`).join("")
    : `<tr><td colspan="5">No questions yet. Press A to ask one.</td></tr>`;
}

/* ------------------------------------------------------------------- data */
async function loadMatch(id) {
  setPlaying(false);
  S.data = null;
  $("play").disabled = true;
  $("caption").textContent = "Preparing the commentary…";
  $("previous").textContent = "";
  const r = await fetch(`/api/timeline/${id}`);
  if (!r.ok) { $("caption").textContent = `Couldn't load that match (${r.status}). Pick another one.`; return; }
  const d = await r.json();
  S.data = d;
  S.squad = null; S.touchIdx = 0;
  fetch(`/api/players/${id}`).then((x) => x.ok ? x.json() : null).then((p) => { if (S.data === d) S.squad = p; drawPitch(); })
    .catch(() => {});
  const tl = d.timeline;
  const k = tl.filter((u) => u.kind === "kickoff");
  S.markers = {
    kickoff1: k[0] && k[0].start, kickoff2: k[1] && k[1].start,
    halfTime: (tl.find((u) => u.kind === "half_time") || {}).start,
    fullTime: (tl.find((u) => u.kind === "full_time") || {}).start,
  };
  S.moments = tl.filter((u) => MOMENT_KINDS.has(u.kind));
  $("home-name").textContent = d.match.home.name;
  $("away-name").textContent = d.match.away.name;
  const hl = d.segments.reduce((s, g) => s + (g.end - g.start), 0);
  $("hl-len").textContent = `(about ${Math.round(hl / 60)} minutes)`;
  S.segIdx = 0; S.finished = false;
  S.mode === "highlights" ? seek(d.segments[0].start) : seek(0);
  $("caption").textContent = `${d.match.home.name} against ${d.match.away.name}. Press Play.`;
  $("play").disabled = false;
  renderHood(d);
  // recording helper: ?at=63 starts the full match at the 63rd minute (only on the first load)
  const at = S.firstLoad ? parseInt(params.get("at") || "", 10) : NaN;
  if (!Number.isNaN(at)) {
    setMode("full");
    seek(timeAtMinute(at));
    $("caption").textContent = `${d.match.home.name} against ${d.match.away.name}, from the ${ordinal(at)} minute. Press Play.`;
  }
  const keep = S.firstLoad ? ["mode", "voice", "pitch", "at"].filter((k) => params.has(k)).map((k) => `&${k}=${encodeURIComponent(params.get(k))}`).join("") : "";
  history.replaceState(null, "", `?match=${id}${keep}`);
  S.firstLoad = false;
}

function ordinal(n) {
  const s = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" })[n % 10] || "th";
  return `${n}${s}`;
}

function timeAtMinute(m) {
  const per = m > 45 ? 2 : 1;
  const hit = S.data.snapshots.find((s) => s.period === per && parseInt(s.clock, 10) >= m);
  return hit ? Math.max(0, hit.t - 3) : 0;
}

function setMode(mode) {
  S.mode = mode;
  const r = document.querySelector(`input[name="mode"][value="${mode}"]`);
  if (r) r.checked = true;
}

function renderHood(d) {
  const m = d.metrics;
  const log = d.colour_log;
  const proposed = log.filter((c) => c.proposed !== undefined);
  const rejected = log.filter((c) => c.verdict === "rejected");
  const row = (k, v) => `<tr><th scope="row">${k}</th><td class="num">${v}</td></tr>`;
  const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
  const lines = log.filter((c) => c.final || c.verdict === "rejected").slice(0, 40).map((c) => {
    const when = c.trigger === "prematch" ? "pre-match" : `${c.facts.minute}'`;
    const v = c.verdict === "rejected"
      ? `<span class="verdict-rejected">rejected: ${esc((c.problems || []).join("; "))}</span><br>proposed: “${esc(c.proposed)}”<br>said instead: “${esc(c.final || "nothing")}”`
      : `“${esc(c.final)}”`;
    return `<tr><td>${when}</td><td>${esc(c.trigger.replace("_", " "))}</td><td>${esc(c.source || "")}</td><td>${v}</td></tr>`;
  }).join("");
  $("hood-body").innerHTML = `
    <p>The agents only ever know what has already happened. These numbers come from this match's commentary.</p>
    <table><tbody>
      ${row("Goals announced", `${m.goals_announced} of ${m.goals}`)}
      ${row("Slowest goal call after the ball went in", m.goal_latency_max_s === null ? "none" : `${m.goal_latency_max_s} s`)}
      ${row("Shots described", `${m.shots_narrated} of ${m.shots}`)}
      ${row("Share of the match with commentary", `${m.airtime_pct}%`)}
      ${row("Longest silence while the ball was in play", `${m.longest_silence_in_play_s} s`)}
      ${row("Lines cut short by something more urgent", m.interruptions)}
      ${row("Co-commentator", esc(d.backend))}
      ${row("Co-commentator lines checked by the verifier", proposed.length ? `${proposed.length - rejected.length} accepted, ${rejected.length} rejected` : `${log.filter((c) => c.final).length} template lines, all checked`)}
    </tbody></table>
    <h3>Questions asked</h3>
    <table><thead><tr><th>When</th><th>Question</th><th>Answer</th><th>Tools the agent used</th><th>Result</th></tr></thead>
    <tbody id="ask-log"></tbody></table>
    <h3>What the co-commentator said, and what the verifier stopped</h3>
    <table><thead><tr><th>When</th><th>Moment</th><th>Source</th><th>Line</th></tr></thead><tbody>${lines}</tbody></table>`;
  renderAskLog();
}

async function init() {
  try { S.config = await (await fetch("/api/config")).json(); } catch (e) { S.config = { tts: "browser" }; }
  const az = $("voice").querySelector('option[value="azure"]');
  if (S.config.tts !== "azure") { az.disabled = true; az.textContent = "Neural voice (Azure, not set up)"; }
  if ("speechSynthesis" in window) {
    Browser.pickVoice();
    speechSynthesis.onvoiceschanged = () => Browser.pickVoice();
  } else {
    $("voice").value = "screenreader"; S.voice = "screenreader";
  }

  const ms = await (await fetch("/api/matches")).json();
  const sel = $("match");
  const rec = document.createElement("optgroup"); rec.label = "Recommended";
  const groups = {};
  for (const m of ms) {
    const o = document.createElement("option");
    o.value = m.match_id;
    o.textContent = `${m.home} v ${m.away}, week ${m.matchweek}`;
    if (m.recommended) rec.appendChild(o.cloneNode(true));
    (groups[m.matchweek] = groups[m.matchweek] || []).push(o);
  }
  sel.appendChild(rec);
  for (const [mw, opts] of Object.entries(groups)) {
    const g = document.createElement("optgroup"); g.label = `Matchweek ${mw}`;
    opts.forEach((o) => g.appendChild(o));
    sel.appendChild(g);
  }
  // recording helpers: ?mode=full|highlights  ?voice=browser|azure|screenreader  ?pitch=1  ?at=<minute>
  if (["full", "highlights"].includes(params.get("mode"))) setMode(params.get("mode"));
  const v = params.get("voice");
  if (["browser", "azure", "screenreader"].includes(v) && !$("voice").querySelector(`option[value="${v}"]`).disabled) {
    $("voice").value = v; S.voice = v;
  }
  if (params.get("pitch") === "1") { S.pitchShown = true; $("pitch-frame").dataset.shown = "true"; }
  S.firstLoad = true;
  const start = (params.get("match") || "M059").toUpperCase();
  sel.value = start;
  sel.addEventListener("change", () => loadMatch(sel.value));
  document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", () => {
    S.mode = r.value;
    if (!S.data) return;
    S.finished = false;
    S.mode === "highlights" ? jumpToSegment(0) : seek(0);
  }));
  $("voice").addEventListener("change", (e) => {
    stopSpeech(); S.voice = e.target.value;
    $("caption").setAttribute("aria-live", "off");
    if (S.voice === "azure") Audio.init();
  });
  $("play").addEventListener("click", togglePlay);
  $("next").addEventListener("click", () => moment(1));
  $("prev").addEventListener("click", () => moment(-1));
  $("ask-score").addEventListener("click", askScore);
  $("ask-win").addEventListener("click", askWin);
  $("ask-stats").addEventListener("click", askStats);
  $("repeat").addEventListener("click", repeatLast);
  $("pitch-frame").addEventListener("click", togglePitch);
  $("ask-form").addEventListener("submit", (e) => { e.preventDefault(); askQuestion($("ask-input").value); });
  if (Rec) { $("ask-mic").hidden = false; $("ask-mic").addEventListener("click", listen); }
  $("ask-input").addEventListener("keydown", (e) => { if (e.key === "Escape") { e.target.blur(); } });

  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "select" || tag === "input" || tag === "textarea") return;
    const k = e.key.toLowerCase();
    const map = {
      " ": togglePlay, s: askScore, w: askWin, p: askStats, l: askBall, r: repeatLast, n: () => moment(1),
      b: () => moment(-1), h: help, v: togglePitch, a: listen,
      c: () => { S.crowdOn = !S.crowdOn; say(S.crowdOn ? "Crowd sound on." : "Crowd sound off."); },
      "+": () => { S.rate = Math.min(1.6, +(S.rate + 0.1).toFixed(1)); say("Faster."); },
      "=": () => { S.rate = Math.min(1.6, +(S.rate + 0.1).toFixed(1)); say("Faster."); },
      "-": () => { S.rate = Math.max(0.7, +(S.rate - 0.1).toFixed(1)); say("Slower."); },
    };
    if (map[k]) {
      if (k === " " && tag === "button") return;   // a focused button already handles Space
      e.preventDefault();
      map[k]();
    }
  });

  setInterval(tick, 100);
  await loadMatch(start);
}

init();
