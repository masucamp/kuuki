// Kuuki demo UI: renders the engine events streamed from app/server.py.
"use strict";

const PROBE_LABEL = {
  has_error: "Contradicts the known facts",
  is_vague: "Vague instruction",
  off_agenda: "Off the agenda",
  needs_silent_expert: "A silent expert fits",
  open_question: "Open question to the room",
  is_response_to_request: "Reply to a request",
  polite_refusal: "Sounds like a polite no",
};
const MOVE = {
  correct: "Corrects a factual error",
  clarify: "Asks what was meant",
  invite_quiet: "Invites a silent expert",
  refocus: "Brings the talk back to the agenda",
};
const DELIVERY = {
  direct: "in the room",
  cushioned: "in the room, softly",
  private_dm_later: "as a private note after the meeting",
};
const MODEL_NAME = { sonnet: "Claude Sonnet 5.5", haiku: "Claude Haiku 5.5" };
// English first by default (the audience does not read Japanese); ?lang=ja puts Japanese first.
const LANG = new URLSearchParams(location.search).get("lang") === "ja" ? "ja" : "en";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
// a line with its translation: the reader's language large, the other one small below
const bi = (ja, en) => {
  const [main, sub] = LANG === "en" && en ? [en, ja] : [ja, en];
  const tag = (s) => (s === ja ? ' lang="ja"' : "");
  return `<p class="primary"${tag(main)}>${esc(main)}</p>` + (sub ? `<p class="secondary"${tag(sub)}>${esc(sub)}</p>` : "");
};
const pick = (ja, en) => (LANG === "en" && en ? en : ja);
const ms = (v) => (v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);

const state = {
  ws: null,
  status: "idle", // idle | playing | paused | done
  mode: "hybrid",
  meeting: null,
  people: {},
  utt: {}, // i -> {article, trace}
  deciderMs: [],
  routes: { silent: 0, act: 0, escalate: 0 },
  cost: 0,
  followups: 0,
  muted: false,
};

// ------------------------------------------------------------------ voices
// One clip at a time, in order.
const voice = { queue: [], playing: null };

function enqueueAudio(src) {
  voice.queue.push(src);
  if (!voice.playing) playNext();
}

function playNext() {
  const src = voice.queue.shift();
  if (!src) { voice.playing = null; return; }
  const a = new Audio(src);
  a.muted = state.muted;
  voice.playing = a;
  a.addEventListener("ended", playNext);
  a.addEventListener("error", playNext);
  a.play().catch(playNext);
}

function stopAudio() {
  voice.queue = [];
  if (voice.playing) { voice.playing.pause(); voice.playing = null; }
}

// ------------------------------------------------------------------ socket

function connect() {
  return new Promise((resolve) => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) return resolve(state.ws);
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => resolve(ws);
    ws.onmessage = (m) => handle(JSON.parse(m.data));
    ws.onclose = () => {
      if (state.status === "playing" || state.status === "paused") toast("Connection to the Kuuki server closed.");
      setStatus(state.status === "done" ? "done" : "idle");
    };
    state.ws = ws;
  });
}

async function send(msg) {
  const ws = await connect();
  ws.send(JSON.stringify(msg));
}

async function start(auto = true) {
  reset();
  setStatus(auto ? "playing" : "paused");
  await send({ cmd: "start", scenario: "demo_meeting_ja.yaml", mode: state.mode, auto, gap: 2.6 });
}

// ------------------------------------------------------------------ controls

function setStatus(s) {
  state.status = s;
  const icon = $("btn-play").querySelector("i");
  icon.className = s === "playing" ? "ph ph-pause" : "ph ph-play";
  $("btn-play").setAttribute("aria-label", s === "playing" ? "Pause" : "Play");
}

function playPause() {
  if (state.status === "idle" || state.status === "done") return start(true);
  if (state.status === "playing") { send({ cmd: "auto", on: false }); setStatus("paused"); }
  else { send({ cmd: "auto", on: true }); setStatus("playing"); }
}

function step() {
  if (state.status === "idle" || state.status === "done") return start(false);
  send({ cmd: "next" });
}

$("btn-play").addEventListener("click", playPause);
$("btn-step").addEventListener("click", step);
$("btn-restart").addEventListener("click", () => start(true));
$("btn-mute").addEventListener("click", toggleMute);

function toggleMute() {
  state.muted = !state.muted;
  if (voice.playing) voice.playing.muted = state.muted;
  $("btn-mute").querySelector("i").className = state.muted ? "ph ph-speaker-slash" : "ph ph-speaker-high";
  $("btn-mute").setAttribute("aria-label", state.muted ? "Unmute voices" : "Mute voices");
}
document.querySelectorAll(".segmented button").forEach((b) =>
  b.addEventListener("click", () => {
    state.mode = b.dataset.mode;
    document.querySelectorAll(".segmented button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    if (state.status !== "idle") start(state.status !== "paused");
  }),
);
document.addEventListener("keydown", (e) => {
  if (e.target.closest("button") && (e.key === " " || e.key === "Enter")) return;
  if (e.key === " ") { e.preventDefault(); playPause(); }
  else if (e.key === "ArrowRight") step();
  else if (e.key === "r" || e.key === "R") start(true);
  else if (e.key === "m" || e.key === "M") toggleMute();
});

// ------------------------------------------------------------------ rendering

function reset() {
  stopAudio();
  Object.assign(state, { utt: {}, deciderMs: [], routes: { silent: 0, act: 0, escalate: 0 }, cost: 0, followups: 0 });
  $("transcript").innerHTML = "";
  $("followups").innerHTML = '<li class="none">Nothing queued yet.</li>';
  $("verdict").textContent = "Waiting for the first utterance";
  $("verdict").classList.remove("hot");
  $("verdict-sub").textContent = "";
  ["silent", "act", "escalate"].forEach((r) => ($(`n-${r}`).textContent = "0"));
  document.querySelectorAll(".outcome").forEach((o) => o.classList.remove("now"));
  setNodes({});
  updateStats();
  $("toast").hidden = true;
}

function onMeeting(ev) {
  state.meeting = ev;
  $("meeting-title").textContent = pick(ev.title, ev.title_en);
  $("meeting-agenda").textContent = pick(ev.agenda, ev.agenda_en);
  $("flow-llm").textContent = ev.judge ? MODEL_NAME[ev.judge] : "off";
  $("node-llm").style.opacity = ev.judge ? "" : "0.4";
  $("clock").innerHTML = `<b>0</b> / ${ev.duration_min} min`;

  const people = $("people");
  people.innerHTML = "";
  state.people = {};
  for (const p of ev.participants) {
    p.display = pick(p.name, p.name_en);
    p.roleDisplay = pick(p.role, p.role_en);
    const chip = el("div", "person silent");
    chip.innerHTML =
      `<span class="avatar">${esc(initial(p.display))}</span><span class="who">${esc(p.display)}</span>` +
      `<span class="role">${esc(p.roleDisplay)}</span>` +
      (p.senior ? '<span class="tag">senior</span>' : "") +
      (p.overseas ? '<span class="tag">EN</span>' : "");
    chip.title = `${p.name} ${p.role}`;
    people.append(chip);
    state.people[p.id] = { ...p, chip };
  }

  const facts = $("facts");
  const factList = LANG === "en" && ev.facts_en ? ev.facts_en : ev.facts;
  facts.hidden = !factList.length;
  facts.innerHTML = "<b>Known facts</b>" + factList.map((f) => `<span>${esc(f)}</span>`).join("");

  const probes = $("probes");
  probes.innerHTML = "";
  for (const p of ev.probes) {
    const row = el("div", "probe");
    row.dataset.id = p.id;
    row.dataset.threshold = p.threshold ?? "";
    if (p.gray) { row.dataset.lo = p.gray[0]; row.dataset.hi = p.gray[1]; }
    row.innerHTML =
      `<span class="label">${PROBE_LABEL[p.id] || p.id}</span><span class="val">-</span>` +
      `<span class="track">` +
      (p.gray ? `<span class="gray" style="left:${p.gray[0] * 100}%;width:${(p.gray[1] - p.gray[0]) * 100}%"></span>` : "") +
      `<span class="fill"></span>` +
      (p.threshold != null ? `<span class="tick" style="left:calc(${p.threshold * 100}% - 1px)"></span>` : "") +
      `</span>`;
    probes.append(row);
  }
  updateStats();
}

function initial(name) {
  return name.trim().charAt(0).toUpperCase();
}

function onUtterance(ev) {
  $("empty")?.remove();
  document.querySelectorAll(".utt.latest").forEach((u) => u.classList.remove("latest"));
  const p = state.people[ev.speaker];
  Object.values(state.people).forEach((x) => x.chip.classList.remove("speaking"));
  if (p) p.chip.classList.remove("silent"), p.chip.classList.add("speaking");

  const art = el("article", "utt latest");
  art.innerHTML =
    `<div class="utt-time">${ev.minute}'</div>` +
    `<span class="avatar">${esc(initial(p?.display ?? "?"))}</span>` +
    `<div class="utt-body">` +
    `<div class="utt-head"><span class="name">${esc(p?.display)}</span><span class="role">${esc(p?.roleDisplay)}</span></div>` +
    bi(ev.text, ev.en) +
    `<div class="trace"><span class="step pending">System 1 reading</span></div>` +
    `</div>`;
  $("transcript").append(art);
  state.utt[ev.i] = { art, trace: art.querySelector(".trace"), body: art.querySelector(".utt-body") };
  scrollDown();
  if (ev.audio) {
    enqueueAudio(ev.audio.src);
    setTrace(ev.i, [["speaking", "pending"]]);
  }

  $("clock").innerHTML = `<b>${ev.minute}</b> / ${state.meeting.duration_min} min`;
  $("probes").classList.add("reading");
  for (const row of $("probes").children) row.classList.remove("fired", "unsure");
  $("verdict").textContent = ev.audio ? "Listening" : "Reading the room";
  $("verdict").classList.remove("hot");
  $("verdict-sub").textContent = ev.audio
    ? "Kuuki reads the room once the speaker finishes"
    : "strands-decider answers seven yes/no probes in one batch";
  document.querySelectorAll(".outcome").forEach((o) => o.classList.remove("now"));
  setNodes(ev.audio ? {} : { "node-decider": "waiting on" });
  updateStats(ev.i + 1);
}

function onProbes(ev) {
  state.deciderMs.push(ev.ms);
  $("probes").classList.remove("reading");
  $("meter-ms").textContent = `strands-decider 2B, ${ms(ev.ms)}`;
  $("flow-ms").textContent = ms(ev.ms);
  for (const row of $("probes").children) {
    const v = ev.probs[row.dataset.id];
    if (v == null) continue;
    row.querySelector(".fill").style.width = `${(v * 100).toFixed(1)}%`;
    row.querySelector(".val").textContent = v.toFixed(2);
    const t = parseFloat(row.dataset.threshold);
    row.classList.toggle("over", !Number.isNaN(t) && v >= t);
  }
  setTrace(ev.i, [["System 1 " + ms(ev.ms), ""]]);
  setNodes({ "node-decider": "on", "node-policy": "on" });
  updateStats();
}

function onEscalating(ev) {
  for (const row of $("probes").children) row.classList.toggle("unsure", row.dataset.id in ev.gray);
  const why = Object.entries(ev.gray).map(([k, v]) => `${PROBE_LABEL[k] || k} ${v.toFixed(2)}`).join(", ");
  $("verdict").textContent = `Not sure. Asking ${MODEL_NAME[ev.model]}`;
  $("verdict").classList.add("hot");
  $("verdict-sub").textContent = `In the gray zone: ${why}`;
  setNodes({ "node-decider": "on", "node-policy": "on", "node-llm": "hot waiting" });
  addTrace(ev.i, `unsure, asking ${ev.model}`, "pending hot");
}

function onJudge(ev) {
  const t = state.utt[ev.i]?.trace;
  t?.querySelector(".pending")?.remove();
  addTrace(ev.i, `${ev.model} ${ms(ev.ms)}`, "hot");
  setNodes({ "node-decider": "on", "node-policy": "on", "node-llm": "hot" });
}

function onDecision(ev) {
  state.routes[ev.route] += 1;
  $(`n-${ev.route}`).textContent = state.routes[ev.route];
  document.querySelectorAll(".outcome").forEach((o) => o.classList.toggle("now", o.dataset.route === ev.route));
  if (ev.cost_usd != null) state.cost = ev.cost_usd;

  let title, sub;
  if (ev.move !== "stay_silent") {
    title = MOVE[ev.move];
    sub = `Delivered ${DELIVERY[ev.delivery]}`;
  } else if (ev.tatemae) {
    title = "Spots a polite no";
    sub = "Kuuki stays quiet in the room and explains it to overseas members";
  } else {
    title = "Stays silent";
    sub = ev.route === "escalate" ? "The LLM agreed there was nothing to add" : "Nothing crossed a threshold";
  }
  if (ev.move !== "stay_silent" && ev.tatemae) sub += ", and spots a polite no";
  $("verdict").textContent = title;
  $("verdict").classList.toggle("hot", ev.move !== "stay_silent" || ev.tatemae);
  $("verdict-sub").textContent = sub;
  // amber only for the probes that actually decided it (named in the policy's reasons)
  if (ev.route !== "escalate") {
    for (const row of $("probes").children) {
      row.classList.toggle("fired", ev.reasons.some((r) => r.includes(`${row.dataset.id}=`) && r.includes("->")));
    }
  }
  if (ev.route !== "escalate") setNodes({ "node-decider": "on", "node-policy": ev.move !== "stay_silent" || ev.tatemae ? "hot" : "on" });

  const label = ev.move !== "stay_silent" ? `${ev.move.replace("_", " ")}, ${ev.delivery.replaceAll("_", " ")}` : ev.tatemae ? "polite no" : "stay silent";
  addTrace(ev.i, label, ev.move !== "stay_silent" || ev.tatemae ? "hot" : "");
  updateStats();
}

function onKuuki(ev) {
  if (ev.cost_usd != null) state.cost = ev.cost_usd;
  const u = state.utt[ev.i];
  if (!u) return;
  let block;
  if (ev.kind === "room") {
    block = el("div", "kuuki room");
    block.innerHTML =
      `<div class="kuuki-head"><i class="ph ph-wind"></i>Kuuki<span class="dim">in the room, ${ms(ev.ms)}</span></div>` +
      bi(ev.text, ev.en);
  } else if (ev.kind === "air") {
    const to = (ev.to || []).join(", ") || "overseas members";
    block = el("div", "kuuki air");
    block.innerHTML =
      `<div class="kuuki-head"><i class="ph ph-translate"></i>Air translation<span class="dim">for ${esc(to)}</span></div>` +
      `<p class="air-text">${esc(ev.text)}</p>`;
  } else if (ev.kind === "dm") {
    block = el("div", "kuuki dm");
    block.innerHTML = `<div class="kuuki-head"><i class="ph ph-lock-simple"></i>Private note to <span>${esc(pick(ev.to, ev.to_en))}</span> after the meeting</div>`;
    addFollowup(ev);
  } else {
    block = el("div", "kuuki followup");
    block.innerHTML = `<div class="kuuki-head"><i class="ph ph-flag"></i>Follow-up added for after the meeting</div>`;
    addFollowup(ev);
  }
  u.body.append(block);
  scrollDown();
  updateStats();
}

function addFollowup(ev) {
  const list = $("followups");
  list.querySelector(".none")?.remove();
  const li = el("li", "item");
  const icon = ev.kind === "dm" ? "ph-lock-simple" : "ph-flag";
  const what = ev.kind === "dm" ? "Private note to" : "Follow up with";
  li.innerHTML =
    `<div class="item-head"><i class="ph ${icon}"></i>${what} <span class="to">${esc(pick(ev.to, ev.to_en))}</span></div>` +
    bi(ev.text, ev.en);
  list.append(li);
  li.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function onDone(ev) {
  state.cost = ev.cost_usd;
  setStatus("done");
  $("verdict").textContent = "Meeting over";
  $("verdict").classList.remove("hot");
  const r = ev.routes;
  $("verdict-sub").textContent = `${r.silent} silent, ${r.act} acted, ${r.escalate} handed to the LLM`;
  document.querySelectorAll(".outcome").forEach((o) => o.classList.remove("now"));
  setNodes({});
  updateStats();
}

// ------------------------------------------------------------------ helpers

function setNodes(map) {
  for (const id of ["node-decider", "node-policy", "node-llm"]) {
    $(id).className = "node " + (map[id] || "");
  }
}

function setTrace(i, steps) {
  const t = state.utt[i]?.trace;
  if (!t) return;
  t.innerHTML = steps.map(([text, cls]) => `<span class="step ${cls}">${esc(text)}</span>`).join("");
}

function addTrace(i, text, cls) {
  const t = state.utt[i]?.trace;
  if (t) t.insertAdjacentHTML("beforeend", `<span class="step ${cls}">${esc(text)}</span>`);
}

function scrollDown() {
  const t = $("transcript");
  requestAnimationFrame(() => (t.scrollTop = t.scrollHeight));
}

function updateStats(progress) {
  const total = state.meeting?.count ?? 0;
  if (progress !== undefined) $("s-progress").textContent = `${progress}/${total}`;
  else if (!state.deciderMs.length) $("s-progress").textContent = `0/${total}`;
  const n = state.routes.silent + state.routes.act + state.routes.escalate;
  $("s-local").textContent = n ? `${Math.round(((state.routes.silent + state.routes.act) / n) * 100)}%` : "-";
  const sorted = [...state.deciderMs].sort((a, b) => a - b);
  $("s-median").textContent = sorted.length ? ms(sorted[Math.floor(sorted.length / 2)]) : "-";
  $("s-cost").textContent = `$${state.cost.toFixed(3)}`;
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 8000);
}

function handle(ev) {
  switch (ev.type) {
    case "meeting": return onMeeting(ev);
    case "utterance": return onUtterance(ev);
    case "probes": return onProbes(ev);
    case "escalating": return onEscalating(ev);
    case "judge": return onJudge(ev);
    case "decision": return onDecision(ev);
    case "kuuki": return onKuuki(ev);
    case "done": return onDone(ev);
    case "audio": return enqueueAudio(ev.src);
    case "error": setStatus("idle"); return toast(ev.message);
  }
}

connect();
