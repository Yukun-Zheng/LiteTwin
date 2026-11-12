"use strict";

const tasks = {
  open_drawer: ["Open Drawer", "The gripper loses contact with the drawer during the pull."],
  insert_square_peg: ["Insert Square Peg", "The first grasp misses the square peg before insertion."],
  handover_item: ["Handover Item", "Two arms must complete a transfer after the grasp is disturbed."]
};
const videos = Array.from(document.querySelectorAll("video"));
const labels = Array.from(document.querySelectorAll(".status"));
const playButton = document.querySelector("#play");
const seek = document.querySelector("#seek");
const clock = document.querySelector("#clock");
let pairs = [];
let playing = false;
let generation = 0;
let requests = new AbortController();
const objectURLs = new Map();

function seconds(value) {
  const n = Math.floor(Math.max(0, value));
  return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0");
}
function ready() {
  return videos.every(v => !v.error && Number.isFinite(v.duration) && v.readyState >= 2);
}
function render() {
  const loaded = ready() && videos.every(v => !v.dataset.failed);
  const time = Math.max(...videos.map(v => v.currentTime));
  const total = Math.max(...videos.map(v => Number.isFinite(v.duration) ? v.duration : 0));
  if (videos.every(v => v.ended)) playing = false;
  playButton.disabled = !loaded;
  seek.disabled = !loaded;
  playButton.textContent = playing ? "Pause pair" : total && time >= total - 0.025 ? "Replay pair" : "Play pair";
  seek.max = total || 1;
  seek.value = time;
  clock.textContent = seconds(time) + " / " + (total ? seconds(total) : "–:––");
  seek.setAttribute("aria-valuetext", clock.textContent);
  videos.forEach((v, index) => {
    labels[index].textContent = v.error || v.dataset.failed ? "Could not load video · open the MP4" :
      v.readyState < 2 ? "Loading…" : v.seeking ? "Seeking…" :
      v.ended ? "Finished" : !v.paused ? "Playing" : v.currentTime ? "Paused" : "Ready";
  });
}
function pausePair() {
  playing = false;
  videos.forEach(v => v.pause());
  render();
}
async function playPair() {
  const ticket = generation;
  const live = videos.filter(v => v.currentTime < v.duration - 0.025);
  if (!live.length) return;
  playing = true;
  try {
    await Promise.all(live.map(v => v.play()));
  } catch (err) {
    if (ticket !== generation || !playing) return;
    pausePair();
    labels.forEach(label => { label.textContent = "Playback interrupted · retry or open the MP4"; });
  }
}
playButton.addEventListener("click", () => {
  if (playing) return pausePair();
  if (videos.every(v => v.currentTime >= v.duration - 0.025)) videos.forEach(v => { v.currentTime = 0; });
  playPair();
});
seek.addEventListener("input", () => {
  const position = Number(seek.value);
  const resume = playing;
  pausePair();
  videos.forEach(v => { v.currentTime = Math.min(position, v.duration); });
  if (resume) playPair();
});
videos.forEach(v => {
  ["loadeddata", "canplay", "durationchange", "pause", "play", "ended", "seeking", "seeked", "timeupdate", "emptied"].forEach(event => v.addEventListener(event, render));
  v.addEventListener("error", pausePair);
});

function showPair() {
  const key = location.hash.slice(1);
  const task = Object.hasOwn(tasks, key) ? key : "open_drawer";
  const pair = pairs.find(row => row.task === task);
  if (!pair) return;
  generation++;
  const ticket = generation;
  requests.abort();
  requests = new AbortController();
  pausePair();
  document.querySelector("#task-name").textContent = tasks[task][0];
  document.querySelector("#scene").textContent = tasks[task][1];
  document.querySelector("#episode").textContent = "Episode " + pair.episode;
  document.querySelectorAll("#tasks button").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.task === task)));
  const outcomes = [pair.open_loop_outcome, pair.litetwin_outcome];
  videos.forEach(async (v, index) => {
    const path = "media/" + task + "/" + ["open_loop", "litetwin"][index];
    const article = v.closest("article");
    article.querySelector(".file").href = path + ".mp4";
    article.querySelector(".outcome").textContent = outcomes[index] === "success" ? "Completed" : "Incomplete";
    v.poster = path + ".jpg";
    delete v.dataset.failed;
    v.removeAttribute("src");
    v.load();
    if (objectURLs.has(v)) {
      URL.revokeObjectURL(objectURLs.get(v));
      objectURLs.delete(v);
    }
    // These small, complete recordings stay seekable on basic static servers.
    try {
      const response = await fetch(path + ".mp4", {signal: requests.signal});
      if (!response.ok) throw new Error("Video request failed");
      const blob = await response.blob();
      if (ticket !== generation) return;
      const url = URL.createObjectURL(blob);
      objectURLs.set(v, url);
      v.src = url;
      v.load();
    } catch (error) {
      if (ticket !== generation) return;
      v.dataset.failed = "true";
      pausePair();
    }
  });
  render();
}
document.querySelectorAll("#tasks button").forEach(button => button.addEventListener("click", () => { location.hash = button.dataset.task; }));
window.addEventListener("hashchange", showPair);
fetch("media/pairs.json").then(response => {
  if (!response.ok) throw new Error("Pair metadata unavailable");
  return response.json();
}).then(data => {
  pairs = data.pairs;
  showPair();
}).catch(() => {
  labels.forEach(label => { label.textContent = "Could not load pair data · open the MP4"; });
});
