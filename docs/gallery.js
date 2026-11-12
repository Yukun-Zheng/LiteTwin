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
const viewer = document.querySelector("#viewer");
const search = document.querySelector("#search");
const menu = document.querySelector("#menu-toggle");
const mobileNav = document.querySelector("#mobile-nav");
const fullscreen = document.querySelector("#fullscreen");
const filterNames = [
  "all",
  "relation_loss",
  "missed_interaction",
  "delay",
  "environment_change",
];
let filter = "all";
let frontOnly = true;
let startWhenReady = false;
let studyStage = 2;
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
    if (locale.language === "zh")
      return locale.maximize().script === "Hant" ? "zh-Hant" : "zh-Hans";
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

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("viewBox", "0 0 24 24");
  const use = document.createElementNS(svg.namespaceURI, "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

function refreshPlayer() {
  const loaded = videos.every(
    (v) =>
      !v.error &&
      !v.dataset.failed &&
      Number.isFinite(v.duration) &&
      v.readyState >= 2,
  );
  const position = Math.max(...videos.map((v) => v.currentTime));
  const total = Math.max(
    ...videos.map((v) => (Number.isFinite(v.duration) ? v.duration : 0)),
  );
  if (videos.every((v) => v.ended)) playing = false;
  playButton.disabled = !loaded;
  seek.disabled = !loaded;
  jump.disabled = !loaded;
  if (catalogFailed)
    document.querySelector("#result-count").textContent = t.dataError;
  if (current?.highlight) {
    const cues = current.highlight.cues;
    const cue =
      [...cues].reverse().find((item) => item.start <= position) || cues[0];
    notes.forEach((note, i) => {
      note.textContent = loaded ? cue[language][i] : "";
    });
  }
  const atEnd = total && position >= total - 0.025;
  const action = playing ? "pause" : atEnd ? "replay" : "play";
  document.querySelector("#play-label").textContent = t[action];
  if (playButton.dataset.action !== action) {
    document
      .querySelector("#play-icon")
      .replaceChildren(icon(action === "replay" ? "redo" : action));
    playButton.dataset.action = action;
  }
  if (
    loaded &&
    startWhenReady &&
    current?.task === "bimanual_lift_tray" &&
    videos.every((v) => v.dataset.loadedTask === current.task)
  ) {
    startWhenReady = false;
    playPair();
  }
  seek.max = total || 1;
  seek.value = position;
  clock.textContent =
    formatTime(position) + " / " + (total ? formatTime(total) : "–:––");
  seek.setAttribute("aria-valuetext", clock.textContent);
  videos.forEach((v, index) => {
    const status = catalogFailed
      ? t.dataError
      : v.error || v.dataset.failed
        ? t.error
        : interrupted
          ? t.interrupted
          : v.readyState < 2
            ? t.loading
            : v.seeking
              ? t.seeking
              : v.ended
                ? t.finished
                : !v.paused
                  ? t.playing
                  : v.currentTime
                    ? t.paused
                    : t.ready;
    if (labels[index].textContent !== status) labels[index].textContent = status;
  });
}
function refreshTask() {
  if (!current) return;
  const entry = t.tasks[current.task];
  const index = pairs.indexOf(current);
  document.querySelector("#task-name").textContent = entry.name;
  document.querySelector("#scene").textContent = entry.description;
  document.querySelector("#episode").textContent =
    t.episode + " " + current.episode + " · " + t[current.disturbance];
  document.querySelector("#baseline-outcome").textContent =
    t[current.open_loop_outcome];
  document.querySelector("#feedback-outcome").textContent =
    t[current.litetwin_outcome];
  const highlight = current.highlight;
  document
    .querySelector("#viewer")
    .classList.toggle("featured-view", Boolean(highlight));
  document.querySelector(".feature-actions").hidden = !highlight;
  notes.forEach((note) => {
    note.hidden = !highlight;
  });
  refreshCamera();
  document.querySelector("#baseline-outcome").dataset.result =
    current.open_loop_outcome;
  document.querySelector("#feedback-outcome").dataset.result =
    current.litetwin_outcome;
  featuredLinks.forEach((link, task) =>
    link.setAttribute("aria-current", String(task === current.task)),
  );
  if (highlight) {
    jump.querySelector("span").textContent =
      t.jump + " " + formatTime(highlight.jump);
    document.querySelector("#annotated").href =
      "highlights/" + current.task + "." + language + ".mp4";
  }
  document.querySelector("#position").textContent =
    index + 1 + " / " + pairs.length;
  picker.value = current.task;
  cards.forEach((card, task) =>
    card.setAttribute("aria-pressed", String(task === current.task)),
  );
}
function refreshCamera() {
  viewer.classList.toggle("front-view", frontOnly);
  const camera = document.querySelector("#camera");
  camera.disabled = false;
  camera.querySelector("span").textContent = frontOnly
    ? t.viewBoth
    : t.viewFront;

  document.querySelector("#capture").textContent =
    frontOnly && current.highlight
      ? t.featuredCapture
      : t[current.capture_profile];
  if (frontOnly && !current.highlight) {
    document.querySelector("#capture").textContent = t[
      current.capture_profile
    ].replace(t.bothCameraLabel, t.frontCapture);
  }
  videos[0].setAttribute("aria-label", frontOnly ? t.frontBase : t.baseView);
  videos[1].setAttribute(
    "aria-label",
    frontOnly ? t.frontFeedback : t.feedbackView,
  );
}

function refreshLibrary() {
  const query = search.value.trim().toLocaleLowerCase();
  let visible = 0;
  for (const pair of pairs) {
    const name = t.tasks[pair.task].name;
    const fields = [name, pair.task.replaceAll("_", " "), t[pair.disturbance]];
    const group = ["actuation_delay", "coordination_delay"].includes(
      pair.disturbance,
    )
      ? "delay"
      : pair.disturbance;
    const matches =
      (filter === "all" || filter === group) &&
      fields.some((field) => field.toLocaleLowerCase().includes(query));
    cards.get(pair.task).hidden = !matches;
    if (matches) visible++;
  }
  document.querySelector("#result-count").textContent = t.results
    .replace("{n}", visible)
    .replace("{total}", pairs.length);
  document.querySelector(".empty").hidden = visible > 0 || !pairs.length;
  for (const button of document.querySelectorAll("#filters button")) {
    button.textContent = t[button.dataset.filter];
    button.setAttribute(
      "aria-pressed",
      String(filter === button.dataset.filter),
    );
  }
}

function refreshLanguage() {
  t = translations[language];
  document.documentElement.lang = language;
  document.title = t.title;
  languagePicker.value = language;
  document.querySelectorAll("[data-text]").forEach((el) => {
    el.textContent = t[el.dataset.text];
  });
  document.querySelectorAll("[data-label]").forEach((el) => {
    el.setAttribute("aria-label", t[el.dataset.label]);
  });
  const suffix = language === "en" ? "" : "." + language;
  const image = "figures/execution-timeline" + suffix + ".svg";
  document.querySelectorAll(".trace-link").forEach((a) => {
    a.href = image;
  });
  const trace = document.querySelector("#trace-image");
  trace.src = image;
  trace.alt = t.traceAlt;
  const guide =
    language === "en" ? "" : "/blob/main/README." + language + ".md";
  document.querySelector("#guide").href =
    "https://github.com/Yukun-Zheng/LiteTwin" + guide + "#run-open-drawer";
  pairs.forEach((pair, i) => {
    const name = t.tasks[pair.task].name;
    picker.options[i].text = name;
    if (featuredLinks.has(pair.task)) {
      const link = featuredLinks.get(pair.task);
      link.querySelector("strong").textContent = t.featureNames[pair.task];
      link.querySelector("small").textContent = t[pair.disturbance];
    }
    const card = cards.get(pair.task);
    card.querySelector("strong").textContent = name;
    card.querySelector(".task-fault").textContent = t[pair.disturbance];
  });
  refreshStudy();
  search.placeholder = t.search;
  menu.setAttribute("aria-label", mobileNav.hidden ? t.menuOpen : t.menuClose);
  refreshExpanded();
  refreshLibrary();
  refreshTask();
  refreshPlayer();
}
languagePicker.addEventListener("change", () => {
  language = languagePicker.value;
  try {
    localStorage.setItem("litetwin.language", language);
  } catch {}
  const url = new URL(location.href);
  url.searchParams.set("lang", language);
  history.replaceState(history.state, "", url);
  refreshLanguage();
});

function pausePair() {
  playing = false;
  videos.forEach((v) => v.pause());
  refreshPlayer();
}
async function playPair() {
  const ticket = generation;
  const live = videos.filter((v) => v.currentTime < v.duration - 0.025);
  if (!live.length) return;
  interrupted = false;
  playing = true;
  try {
    await Promise.all(live.map((v) => v.play()));
  } catch {
    if (ticket !== generation || !playing) return;
    interrupted = true;
    pausePair();
  }
}
playButton.addEventListener("click", () => {
  if (playing) return pausePair();
  if (videos.every((v) => v.currentTime >= v.duration - 0.025))
    videos.forEach((v) => {
      v.currentTime = 0;
    });
  playPair();
});
function seekPair(position, resume) {
  pausePair();
  videos.forEach((v) => {
    v.currentTime = Math.min(position, v.duration);
  });
  if (resume) playPair();
}
seek.addEventListener("input", () => seekPair(Number(seek.value), playing));
jump.addEventListener("click", () => {
  if (current?.highlight) seekPair(current.highlight.jump, true);
});
videos.forEach((v) => {
  [
    "loadeddata",
    "canplay",
    "durationchange",
    "pause",
    "play",
    "ended",
    "seeking",
    "seeked",
    "timeupdate",
    "emptied",
  ].forEach((event) => v.addEventListener(event, refreshPlayer));
  v.addEventListener("error", pausePair);
});

function showPair() {
  const task = location.hash.slice(1);
  const matched = pairs.find((row) => row.task === task);
  const pair = matched || current || pairs[0];
  if (!pair || current === pair) return;
  current = pair;
  frontOnly = Boolean(pair.highlight);
  if (startWhenReady && pair.task !== "bimanual_lift_tray")
    startWhenReady = false;
  if (matched) viewer.scrollIntoView({ block: "start" });
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
    delete v.dataset.loadedTask;
    v.removeAttribute("src");
    v.load();
    if (objectURLs.has(v)) {
      URL.revokeObjectURL(objectURLs.get(v));
      objectURLs.delete(v);
    }
    // Local static servers can serve the complete clip without supporting range requests.
    try {
      const response = await fetch(path + ".mp4", { signal: requests.signal });
      if (!response.ok) throw new Error("Video request failed");
      const blob = await response.blob();
      if (ticket !== generation) return;
      const url = URL.createObjectURL(blob);
      objectURLs.set(v, url);
      v.dataset.loadedTask = pair.task;
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

picker.addEventListener("change", () => {
  location.hash = picker.value;
});
for (const [id, step] of [
  ["previous", -1],
  ["next", 1],
]) {
  document.getElementById(id).addEventListener("click", () => {
    const index = (pairs.indexOf(current) + step + pairs.length) % pairs.length;
    location.hash = pairs[index].task;
  });
}
window.addEventListener("hashchange", showPair);
window.addEventListener("popstate", () => {
  language = preferredLanguage();
  refreshLanguage();
  showPair();
});
for (const key of filterNames) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.filter = key;
  button.addEventListener("click", () => {
    filter = key;
    refreshLibrary();
  });
  document.querySelector("#filters").append(button);
}
search.addEventListener("input", refreshLibrary);
document.querySelector("#clear").addEventListener("click", () => {
  search.value = "";
  filter = "all";
  refreshLibrary();
  search.focus();
});

document.querySelector("#camera").addEventListener("click", () => {
  if (!current) return;
  frontOnly = !frontOnly;
  refreshCamera();
});
function refreshStudy() {
  for (const [selector, method] of [
    ["#study-left", "open_loop"],
    ["#study-right", "litetwin"],
  ]) {
    const src = "figures/tray-sequence/" + method + "-" + studyStage + ".jpg";
    const img = document.querySelector(selector);
    if (img.getAttribute("src") !== src) img.src = src;
  }
  document.querySelector("#study-time").textContent = [
    "00:03.00",
    "00:08.00",
    "00:09.50",
  ][studyStage];
  document.querySelector("#study-note").textContent = t.studyNotes[studyStage];
  document.querySelectorAll("#study-steps button").forEach((button) => {
    button.setAttribute(
      "aria-pressed",
      String(Number(button.dataset.stage) === studyStage),
    );
  });
}
document.querySelector("#study-steps").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (button) {
    studyStage = Number(button.dataset.stage);
    refreshStudy();
  }
});
document.querySelector("#watch").addEventListener("click", () => {
  viewer.focus({ preventScroll: true });
  viewer.scrollIntoView({ block: "start" });
  if (current?.task !== "bimanual_lift_tray" || playButton.disabled) {
    startWhenReady = true;
    return;
  }
  if (!playing) playButton.click();
});

function setMenu(open) {
  mobileNav.hidden = !open;
  menu.setAttribute("aria-expanded", String(open));
  menu.setAttribute("aria-label", open ? t.menuClose : t.menuOpen);
  menu.replaceChildren(icon(open ? "close" : "menu"));
}
menu.addEventListener("click", () => setMenu(mobileNav.hidden));
mobileNav.addEventListener("click", (event) => {
  const link = event.target.closest("a");
  if (link) {
    setMenu(false);
    const section = document.querySelector(link.hash);
    section.tabIndex = -1;
    section.focus({ preventScroll: true });
  }
});
matchMedia("(min-width:761px)").addEventListener("change", (e) => {
  if (e.matches) setMenu(false);
});

function refreshExpanded() {
  const expanded = Boolean(
    document.fullscreenElement || viewer.classList.contains("expanded"),
  );
  fullscreen.setAttribute("aria-pressed", String(expanded));
  fullscreen.setAttribute("aria-label", expanded ? t.collapse : t.expand);
  fullscreen.title = expanded ? t.collapse : t.expand;
}
function setExpandedFallback(open) {
  viewer.classList.toggle("expanded", open);
  document.body.classList.toggle("player-expanded", open);
  refreshExpanded();
  fullscreen.focus({ preventScroll: true });
}
fullscreen.addEventListener("click", async () => {
  if (document.fullscreenElement) return document.exitFullscreen();
  if (viewer.classList.contains("expanded")) return setExpandedFallback(false);
  if (viewer.requestFullscreen) {
    try {
      await viewer.requestFullscreen();
      return;
    } catch {}
  }
  setExpandedFallback(true);
});
document.addEventListener("fullscreenchange", refreshExpanded);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (!mobileNav.hidden) {
      setMenu(false);
      menu.focus();
    }
    if (viewer.classList.contains("expanded")) setExpandedFallback(false);
  }
});
const sections = [
  ...document.querySelectorAll("#comparisons, #library, #method"),
];
function updateNavigation() {
  const edge = document.querySelector(".site-header").offsetHeight + 40;
  const nearBottom =
    innerHeight + scrollY >= document.documentElement.scrollHeight - 2;
  const active = nearBottom
    ? sections.at(-1)
    : sections
        .filter((section) => section.getBoundingClientRect().top <= edge)
        .at(-1);
  document.querySelectorAll("[data-section]").forEach((link) => {
    if (active && link.dataset.section === active.id)
      link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  });
}
document.addEventListener("scroll", updateNavigation, { passive: true });
window.addEventListener("resize", updateNavigation);
updateNavigation();
refreshLanguage();
fetch("media/pairs.json")
  .then((response) => {
    if (!response.ok) throw new Error("Cannot load pairs");
    return response.json();
  })
  .then((data) => {
    pairs = data.pairs;
    picker.replaceChildren();
    const grid = document.querySelector("#tasks");
    for (const { task, highlight } of pairs) {
      if (highlight) {
        const link = document.createElement("a");
        link.href = "#" + task;
        link.className = "feature-tile";
        const picture = new Image();
        picture.src =
          task === "bimanual_lift_tray"
            ? "figures/tray-sequence/litetwin-2.jpg"
            : "media/" + task + "/detail.jpg";
        picture.alt = "";
        const text = document.createElement("span");
        text.append(
          document.createElement("strong"),
          document.createElement("small"),
        );
        link.append(picture, text, icon("arrow"));
        link.addEventListener("click", () =>
          viewer.scrollIntoView({ block: "start" }),
        );
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
        viewer.focus({ preventScroll: true });
        viewer.scrollIntoView({ block: "start" });
      });
      grid.append(card);
      cards.set(task, card);
    }
    picker.disabled = false;
    document.querySelector("#previous").disabled = false;
    document.querySelector("#next").disabled = false;
    refreshLanguage();
    showPair();
  })
  .catch(() => {
    catalogFailed = true;
    startWhenReady = false;
    refreshPlayer();
  });
