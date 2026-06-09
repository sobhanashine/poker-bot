/* Texas Hold'em Mini App — multiplayer client. */
"use strict";

const BOT_USERNAME = "Poker_mars_bot";
const tg = window.Telegram ? window.Telegram.WebApp : null;
if (tg) { tg.ready(); tg.expand(); }

const SUIT = { s: "♠", h: "♥", d: "♦", c: "♣" };
const RED = new Set(["h", "d"]);

let state = {
  code: localStorage.getItem("pokerCode") || null,
  view: null,
  raiseVal: null,
  pollTimer: null,
  lastJson: "",
  busy: false,
};

/* ---------------- API ---------------- */
async function api(op, extra = {}) {
  const body = Object.assign(
    { op, initData: tg ? tg.initData : "", code: state.code || extra.code },
    extra
  );
  const res = await fetch("/api/play", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

/* ---------------- Lobby ---------------- */
const $ = (id) => document.getElementById(id);

function showLobby(msg, ok) {
  stopPolling();
  $("table").classList.add("hidden");
  $("lobby").classList.remove("hidden");
  const m = $("lobbyMsg");
  m.textContent = msg || "";
  m.className = "msg" + (ok ? " ok" : "");
}

$("btnCreate").onclick = async () => {
  $("btnCreate").disabled = true;
  const r = await api("create");
  $("btnCreate").disabled = false;
  if (r.error) return showLobby(r.error);
  enterTable(r.table);
};

$("btnJoin").onclick = async () => {
  const code = $("codeInput").value.trim().toUpperCase();
  if (code.length < 4) return showLobby("Enter a valid table code.");
  const r = await api("join", { code });
  if (r.error) return showLobby(r.error);
  enterTable(r.table);
};

$("btnLeave").onclick = async () => {
  if (tg) tg.HapticFeedback && tg.HapticFeedback.impactOccurred("light");
  await api("leave");
  localStorage.removeItem("pokerCode");
  state.code = null;
  showLobby("");
};

$("codeChip").onclick = () => {
  if (!state.code) return;
  const link = inviteLink();
  navigator.clipboard && navigator.clipboard.writeText(link);
  flashStage("Link copied!");
};

/* ---------------- Table session ---------------- */
function enterTable(view) {
  state.code = view.code;
  localStorage.setItem("pokerCode", view.code);
  $("lobby").classList.add("hidden");
  $("table").classList.remove("hidden");
  render(view);
  startPolling();
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(poll, 1500);
}
function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}
async function poll() {
  if (state.busy || document.hidden) return;
  const r = await api("state");
  if (r.error && !r.table) {
    if (String(r.error).includes("not found")) {
      localStorage.removeItem("pokerCode");
      state.code = null;
      showLobby("That table no longer exists.");
    }
    return;
  }
  render(r.table || (r.error ? null : r.table));
}

function inviteLink() {
  return `https://t.me/${BOT_USERNAME}?start=${state.code}`;
}

function invite() {
  const link = inviteLink();
  const text = "Join my poker table on Telegram 🎴";
  const share = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(text)}`;
  if (tg) tg.openTelegramLink(share);
  else window.open(share, "_blank");
}

/* ---------------- Rendering ---------------- */
function flashStage(txt) {
  const el = $("stageLabel");
  const prev = el.textContent;
  el.textContent = txt;
  setTimeout(() => { if (state.view) el.textContent = state.view.stage; }, 1200);
}

function cardEl(code, cls = "") {
  if (!code) return `<div class="card back ${cls}"></div>`;
  const rank = code.slice(0, -1);
  const suit = code.slice(-1);
  const r = rank === "T" ? "10" : rank;
  const red = RED.has(suit) ? "red" : "";
  return `<div class="card ${red} ${cls}"><span class="r">${r}</span><span class="s">${SUIT[suit]}</span></div>`;
}

function render(view) {
  if (!view) return;
  state.view = view;
  const json = JSON.stringify(view);
  // Keep raise slider stable; skip full re-render if nothing changed.
  if (json === state.lastJson) return;
  state.lastJson = json;

  $("codeChip").textContent = view.code;
  $("stageLabel").textContent = view.stage;
  $("pot").textContent = "Pot: " + view.pot;

  // Board
  const board = $("board");
  let b = view.board.map((c) => cardEl(c)).join("");
  for (let i = view.board.length; i < 5; i++) b += `<div class="empty-card"></div>`;
  board.innerHTML = b;

  // Opponents
  const opp = $("opponents");
  opp.innerHTML = view.players.filter((p) => !p.is_me).map(seatHtml).join("")
    || `<div class="wait">Waiting for players…</div>`;

  // Me
  renderMe(view);

  // Result banner
  renderResult(view);

  // Controls
  renderControls(view);

  // Log
  $("log").innerHTML = (view.log || []).slice().reverse()
    .map((l) => `<li>${escapeHtml(l)}</li>`).join("");
}

function seatHtml(p) {
  const cls = ["seat"];
  if (p.is_turn) cls.push("turn");
  if (p.folded) cls.push("folded");
  let cards = "";
  if (p.cards) cards = p.cards.map((c) => cardEl(c, "sm")).join("");
  else if (!p.folded && state.view.stage !== "waiting" && state.view.stage !== "hand_over")
    cards = cardEl(null, "sm") + cardEl(null, "sm");
  let tag = "";
  if (p.folded) tag = `<span class="tag fold">folded</span>`;
  else if (p.all_in) tag = `<span class="tag allin">all-in</span>`;
  const dealer = p.is_dealer ? `<span class="badge">D</span>` : "";
  const bet = p.round_bet ? `bet ${p.round_bet}` : "";
  return `<div class="${cls.join(" ")}">${dealer}
    <div class="nm">${escapeHtml(p.name)}</div>
    <div class="ch">${p.chips} 🪙</div>
    <div class="mini-cards">${cards}</div>
    <div class="bet">${bet}</div>${tag}</div>`;
}

function renderMe(view) {
  const me = view.players.find((p) => p.is_me);
  const meEl = $("me");
  if (!me) { meEl.innerHTML = ""; return; }
  meEl.className = "me" + (me.is_turn ? " turn" : "");
  const hand = view.my_cards.length
    ? view.my_cards.map((c) => cardEl(c, "big")).join("")
    : `<div class="empty-card"></div><div class="empty-card"></div>`;
  const bet = me.round_bet ? `in pot: ${me.round_bet}` : (me.all_in ? "all-in" : "");
  meEl.innerHTML = `
    <div class="info">
      <div class="nm">${escapeHtml(me.name)} ${me.is_dealer ? "🔘" : ""}</div>
      <div class="ch">${me.chips} 🪙</div>
      <div class="bet">${bet}</div>
    </div>
    <div class="hand">${hand}</div>`;
}

function renderResult(view) {
  const banner = $("resultBanner");
  if (view.result && (view.stage === "hand_over" || view.stage === "showdown")) {
    const wins = view.result.winners
      .map((w) => `<span class="win">${escapeHtml(w.name)}</span> +${w.amount} (${escapeHtml(w.desc)})`)
      .join("<br>");
    banner.innerHTML = `🏆 ${wins}`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

function renderControls(view) {
  const c = $("controls");

  // Host pre-hand / between-hands controls.
  if (view.stage === "waiting" || view.stage === "hand_over") {
    let html = `<div class="host-actions">`;
    html += `<button class="invite" onclick="invite()">📨 Invite</button>`;
    if (view.can_start) {
      const label = view.stage === "hand_over" ? "Deal next hand" : "Deal hand";
      html += `<button class="deal" onclick="doStart()">🎬 ${label}</button>`;
    }
    html += `</div>`;
    if (!view.can_start) {
      const need = view.is_host
        ? "Need 2+ players with chips to deal."
        : "Waiting for the host to deal…";
      html += `<div class="wait">${need}</div>`;
    }
    c.innerHTML = html;
    return;
  }

  // In a hand.
  if (!view.my_turn || !view.actions) {
    c.innerHTML = `<div class="wait">${view.turn_name
      ? "Waiting for " + escapeHtml(view.turn_name) + "…" : "…"}</div>`;
    return;
  }
  c.innerHTML = actionsHtml(view.actions);
  wireRaise(view.actions);
}

function actionsHtml(a) {
  let raise = "";
  if (a.raise_min != null && a.raise_max > a.raise_min) {
    if (state.raiseVal == null || state.raiseVal < a.raise_min || state.raiseVal > a.raise_max)
      state.raiseVal = a.raise_min;
    raise = `
      <div class="raise-panel">
        <div class="row">
          <input id="raiseRange" type="range" min="${a.raise_min}" max="${a.raise_max}"
                 step="${state.view.big_blind}" value="${state.raiseVal}" />
          <span class="raise-amt" id="raiseAmt">${state.raiseVal}</span>
        </div>
        <div class="quick">
          <button data-amt="${a.raise_min}">Min</button>
          <button data-amt="${potRaise(0.5)}">½ pot</button>
          <button data-amt="${potRaise(1)}">Pot</button>
          <button data-amt="${a.raise_max}">Max</button>
        </div>
      </div>`;
  }

  let row = `<div class="act-row">`;
  row += `<button class="b-fold" onclick="doAct('fold')">Fold</button>`;
  if (a.can_check) row += `<button class="b-check" onclick="doAct('check')">Check</button>`;
  if (a.can_call) row += `<button class="b-call" onclick="doAct('call')">Call ${a.call_amount}</button>`;
  if (a.raise_min != null && a.raise_max > a.raise_min) {
    const verb = a.raise_verb === "bet" ? "Bet" : "Raise";
    row += `<button class="b-raise" onclick="doRaise()">${verb} to <span id="raiseBtnAmt">${state.raiseVal}</span></button>`;
  } else if (a.can_all_in) {
    row += `<button class="b-allin" onclick="doAct('all_in')">All-in ${a.all_in_amount}</button>`;
  }
  row += `</div>`;
  // Always offer all-in as a secondary if raise row is shown.
  if (a.raise_min != null && a.raise_max > a.raise_min && a.can_all_in) {
    row += `<div class="act-row" style="margin-top:8px">
      <button class="b-allin" onclick="doAct('all_in')">All-in ${a.all_in_amount}</button></div>`;
  }
  return raise + row;
}

function potRaise(frac) {
  const v = state.view;
  const a = v.actions;
  const target = v.current_bet + Math.round(v.pot * frac);
  return Math.max(a.raise_min, Math.min(a.raise_max, roundTo(target, v.big_blind)));
}
function roundTo(n, step) { return Math.round(n / step) * step; }

function wireRaise(a) {
  const range = $("raiseRange");
  if (!range) return;
  const update = (val) => {
    state.raiseVal = parseInt(val, 10);
    $("raiseAmt").textContent = state.raiseVal;
    const btn = $("raiseBtnAmt"); if (btn) btn.textContent = state.raiseVal;
  };
  range.oninput = (e) => update(e.target.value);
  document.querySelectorAll(".quick button").forEach((b) => {
    b.onclick = () => { range.value = b.dataset.amt; update(b.dataset.amt); };
  });
}

/* ---------------- Actions ---------------- */
async function doAct(action, amount = 0) {
  state.busy = true;
  if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("medium");
  const r = await api("act", { action, amount });
  state.busy = false;
  if (r.error) { flashStage(r.error.slice(0, 40)); if (r.table) render(r.table); return; }
  state.lastJson = ""; render(r.table);
}
function doRaise() {
  const a = state.view.actions;
  doAct(a.raise_verb || "raise", state.raiseVal);
}
async function doStart() {
  state.busy = true;
  const r = await api("start");
  state.busy = false;
  if (r.error) return flashStage(r.error.slice(0, 40));
  state.lastJson = ""; render(r.table);
}
window.invite = invite;
window.doAct = doAct;
window.doRaise = doRaise;
window.doStart = doStart;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- Boot ---------------- */
(async function boot() {
  const url = new URLSearchParams(location.search);
  const startParam =
    (tg && tg.initDataUnsafe ? tg.initDataUnsafe.start_param : null) ||
    url.get("c") || url.get("tgWebAppStartParam");
  if (startParam) {
    const r = await api("join", { code: startParam.toUpperCase() });
    if (!r.error) return enterTable(r.table);
    showLobby(r.error);
    return;
  }
  if (state.code) {
    const r = await api("state");
    if (r.table) return enterTable(r.table);
    localStorage.removeItem("pokerCode");
    state.code = null;
  }
  showLobby("");
})();

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.code) poll();
});
