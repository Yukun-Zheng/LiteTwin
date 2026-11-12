"use strict";

const videos = Array.from(document.querySelectorAll("video"));
const labels = Array.from(document.querySelectorAll(".status"));
const playButton = document.querySelector("#play");
const seek = document.querySelector("#seek");
const clock = document.querySelector("#clock");
const picker = document.querySelector("#task-select");
const languagePicker = document.querySelector("#language");
const objectURLs = new Map();
const cards = new Map();
const featuredLinks = new Map();
const notes = Array.from(document.querySelectorAll(".frame-note"));
const jump = document.querySelector("#jump");
let pairs = [];
let current;
let playing = false;
let interrupted = false;
let catalogFailed = false;
let generation = 0;
let requests = new AbortController();

function preferredLanguage() {
  const explicit = new URL(location.href).searchParams.get("lang");
  if (Object.hasOwn(translations, explicit)) return explicit;
  try {
    const saved = localStorage.getItem("litetwin.language");
    if (Object.hasOwn(translations, saved)) return saved;
  } catch {}
  for (const tag of navigator.languages) {
    const locale = new Intl.Locale(tag);
    if (locale.language === "zh") return locale.maximize().script === "Hant" ? "zh-Hant" : "zh-Hans";
    if (locale.language === "en") return "en";
  }
  return "en";
}
let language = preferredLanguage();
let t = translations[language];

function formatTime(value) {
  const n = Math.floor(Math.max(0, value));
  return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0");
}

function refreshPlayer() {
  const loaded = videos.every(v => !v.error && !v.dataset.failed && Number.isFinite(v.duration) && v.readyState >= 2);
  const position = Math.max(...videos.map(v => v.currentTime));
  const total = Math.max(...videos.map(v => Number.isFinite(v.duration) ? v.duration : 0));
  if (videos.every(v => v.ended)) playing = false;
  playButton.disabled = !loaded;
  seek.disabled = !loaded;
  jump.disabled = !loaded;
  if (current?.highlight) {
    const cues = current.highlight.cues;
    const cue = [...cues].reverse().find(item => item.start <= position) || cues[0];
    notes.forEach((note, i) => {
      note.textContent = loaded ? cue[language][i] : "";
    });
  }
  playButton.textContent = playing ? t.pause : total && position >= total - 0.025 ? t.replay : t.play;
  seek.max = total || 1;
  seek.value = position;
  clock.textContent = formatTime(position) + " / " + (total ? formatTime(total) : "–:––");
  seek.setAttribute("aria-valuetext", clock.textContent);
  videos.forEach((v, index) => {
    labels[index].textContent = catalogFailed ? t.dataError : v.error || v.dataset.failed ? t.error :
      interrupted ? t.interrupted : v.readyState < 2 ? t.loading : v.seeking ? t.seeking :
      v.ended ? t.finished : !v.paused ? t.playing : v.currentTime ? t.paused : t.ready;
  });
}
function refreshTask() {
  if (!current) return;
  const entry = t.tasks[current.task];
  const index = pairs.indexOf(current);
  document.querySelector("#task-name").textContent = entry.name;
  document.querySelector("#scene").textContent = entry.description;
  document.querySelector("#episode").textContent = t.episode + " " + current.episode + " · " + t[current.disturbance];
  document.querySelector("#baseline-outcome").textContent = t[current.open_loop_outcome];
  document.querySelector("#feedback-outcome").textContent = t[current.litetwin_outcome];
  const highlight = current.highlight;
  document.querySelector("#viewer").classList.toggle("featured-view", Boolean(highlight));
  document.querySelector(".feature-actions").hidden = !highlight;
  notes.forEach(note => { note.hidden = !highlight; });
  document.querySelector("#capture").textContent = highlight ? t.featuredCapture : t[current.capture_profile];
  videos[0].setAttribute("aria-label", highlight ? t.frontBase : t.baseView);
  videos[1].setAttribute("aria-label", highlight ? t.frontFeedback : t.feedbackView);
  featuredLinks.forEach((link, task) => link.setAttribute("aria-current", String(task === current.task)));
  if (highlight) {
    jump.textContent = t.jump + " " + formatTime(highlight.jump);
    document.querySelector("#annotated").href = "highlights/" + current.task + "." + language + ".mp4";
  }
  document.querySelector("#position").textContent = (index + 1) + " / " + pairs.length;
  picker.value = current.task;
  cards.forEach((card, task) => card.setAttribute("aria-pressed", String(task === current.task)));
}
function refreshLanguage() {
  t = translations[language];
  document.documentElement.lang = language;
  document.title = t.title;
  languagePicker.value = language;
  document.querySelectorAll("[data-text]").forEach(el => { el.textContent = t[el.dataset.text]; });
  document.querySelectorAll("[data-label]").forEach(el => { el.setAttribute("aria-label", t[el.dataset.label]); });
  const suffix = language === "en" ? "" : "." + language;
  const image = "figures/execution-timeline" + suffix + ".svg";
  document.querySelectorAll(".trace-link").forEach(a => { a.href = image; });
  const trace = document.querySelector("#trace-image");
  trace.src = image;
  trace.alt = t.traceAlt;
  const guide = language === "en" ? "" : "/blob/main/README." + language + ".md";
  document.querySelector("#guide").href = "https://github.com/Yukun-Zheng/LiteTwin" + guide + "#run-open-drawer";
  pairs.forEach((pair, i) => {
    const name = t.tasks[pair.task].name;
    picker.options[i].text = name;
    if (featuredLinks.has(pair.task)) featuredLinks.get(pair.task).textContent = name;
    const card = cards.get(pair.task);
    card.querySelector("strong").textContent = name;
    card.querySelector(".task-fault").textContent = t[pair.disturbance];
  });
  refreshTask();
  refreshPlayer();
}
languagePicker.addEventListener("change", () => {
  language = languagePicker.value;
  try { localStorage.setItem("litetwin.language", language); } catch {}
  const url = new URL(location.href);
  url.searchParams.set("lang", language);
  history.replaceState(history.state, "", url);
  refreshLanguage();
});

function pausePair() {
  playing = false;
  videos.forEach(v => v.pause());
  refreshPlayer();
}
async function playPair() {
  const ticket = generation;
  const live = videos.filter(v => v.currentTime < v.duration - 0.025);
  if (!live.length) return;
  interrupted = false;
  playing = true;
  try {
    await Promise.all(live.map(v => v.play()));
  } catch {
    if (ticket !== generation || !playing) return;
    interrupted = true;
    pausePair();
  }
}
playButton.addEventListener("click", () => {
  if (playing) return pausePair();
  if (videos.every(v => v.currentTime >= v.duration - 0.025)) videos.forEach(v => { v.currentTime = 0; });
  playPair();
});
function seekPair(position, resume) {
  pausePair();
  videos.forEach(v => { v.currentTime = Math.min(position, v.duration); });
  if (resume) playPair();
}
seek.addEventListener("input", () => seekPair(Number(seek.value), playing));
jump.addEventListener("click", () => {
  if (current?.highlight) seekPair(current.highlight.jump, true);
});
videos.forEach(v => {
  ["loadeddata", "canplay", "durationchange", "pause", "play", "ended", "seeking", "seeked", "timeupdate", "emptied"].forEach(event => v.addEventListener(event, refreshPlayer));
  v.addEventListener("error", pausePair);
});

function showPair() {
  const task = location.hash.slice(1);
  const pair = pairs.find(row => row.task === task) || pairs[0];
  if (!pair || current === pair) return;
  current = pair;
  const ticket = ++generation;
  requests.abort();
  requests = new AbortController();
  interrupted = false;
  pausePair();
  refreshTask();
  videos.forEach(async (v, i) => {
    const path = "media/" + pair.task + "/" + ["open_loop", "litetwin"][i];
    v.closest("article").querySelector(".file").href = path + ".mp4";
    v.poster = path + ".jpg";
    delete v.dataset.failed;
    v.removeAttribute("src");
    v.load();
    if (objectURLs.has(v)) {
      URL.revokeObjectURL(objectURLs.get(v));
      objectURLs.delete(v);
    }
    // Local static servers can serve the complete clip without supporting range requests.
    try {
      const response = await fetch(path + ".mp4", {signal: requests.signal});
      if (!response.ok) throw new Error("Video request failed");
      const blob = await response.blob();
      if (ticket !== generation) return;
      const url = URL.createObjectURL(blob);
      objectURLs.set(v, url);
      v.src = url;
      v.load();
    } catch {
      if (ticket !== generation) return;
      v.dataset.failed = "true";
      pausePair();
    }
  });
  refreshPlayer();
}

picker.addEventListener("change", () => { location.hash = picker.value; });
for (const [id, step] of [["previous", -1], ["next", 1]]) {
  document.getElementById(id).addEventListener("click", () => {
    const index = (pairs.indexOf(current) + step + pairs.length) % pairs.length;
    location.hash = pairs[index].task;
  });
}
window.addEventListener("hashchange", showPair);
window.addEventListener("popstate", () => { language = preferredLanguage(); refreshLanguage(); showPair(); });
refreshLanguage();
fetch("media/pairs.json").then(response => {
  if (!response.ok) throw new Error("Cannot load pairs");
  return response.json();
}).then(data => {
  pairs = data.pairs;
  picker.replaceChildren();
  const grid = document.querySelector("#tasks");
  for (const {task, highlight} of pairs) {
    if (highlight) {
      const link = document.createElement("a");
      link.href = "#" + task;
      document.querySelector("#featured-links").append(link);
      featuredLinks.set(task, link);
    }
    picker.add(new Option("", task));
    const card = document.createElement("button");
    card.type = "button";
    card.dataset.task = task;
    card.className = "task-card";
    const preview = new Image();
    preview.src = "media/" + task + "/preview.jpg";
    preview.loading = "lazy";
    preview.alt = "";
    const fault = document.createElement("span");
    fault.className = "task-fault";
    card.append(preview, document.createElement("strong"), fault);
    card.addEventListener("click", () => {
      location.hash = task;
      picker.focus({preventScroll: true});
      document.querySelector("#viewer").scrollIntoView({block: "start"});
    });
    grid.append(card);
    cards.set(task, card);
  }
  picker.disabled = false;
  document.querySelector("#previous").disabled = false;
  document.querySelector("#next").disabled = false;
  refreshLanguage();
  showPair();
}).catch(() => {
  catalogFailed = true;
  refreshPlayer();
});
