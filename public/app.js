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
  const [sb, bb] = $("optBlinds").value.split(",").map(Number);
  const r = await api("create", {
    small_blind: sb,
    big_blind: bb,
    starting_stack: Number($("optStack").value),
    turn_seconds: Number($("optTimer").value),
  });
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
  state.prevPot = null;
  state.prevBoardLen = view.board ? view.board.length : 0;
  state.prevMyKey = (view.my_cards || []).join("");
  // Don't replay a finished hand's celebration when (re)joining.
  state.lastCelebrated = view.result ? JSON.stringify(view.result.winners) : null;
  state.lastJson = "";
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

function cardEl(code, cls = "", style = "") {
  const st = style ? ` style="${style}"` : "";
  if (!code) return `<div class="card back ${cls}"${st}></div>`;
  const rank = code.slice(0, -1);
  const suit = code.slice(-1);
  const r = rank === "T" ? "10" : rank;
  const red = RED.has(suit) ? "red" : "";
  return `<div class="card ${red} ${cls}"${st}>
    <span class="corner"><b>${r}</b><i>${SUIT[suit]}</i></span>
    <span class="pip">${SUIT[suit]}</span></div>`;
}

function fmt(n) { return Number(n).toLocaleString("en-US"); }

/* ---------------- Jetons + flight animations ---------------- */

const REDUCED_MOTION =
  window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const JETON_DENOMS = [
  [1000, "j-go"], [500, "j-k"], [100, "j-g"], [25, "j-b"], [5, "j-r"], [1, "j-w"],
];

function jetonClass(amount) {
  for (const [v, c] of JETON_DENOMS) if (amount >= v) return c;
  return "j-w";
}

/* Greedy chip breakdown of an amount, capped for readability. */
function jetonChips(amount, max) {
  const chips = [];
  let rem = Math.max(1, amount | 0);
  for (const [v, c] of JETON_DENOMS) {
    while (rem >= v && chips.length < max) { chips.push(c); rem -= v; }
    if (chips.length >= max) break;
  }
  if (!chips.length) chips.push("j-w");
  return chips;
}

function jstackHtml(amount, max = 4) {
  return jetonChips(amount, max)
    .map((c, i) => `<i class="jeton ${c}" style="bottom:${i * 3}px"></i>`)
    .join("");
}

function feltEl() { return document.querySelector("#table .felt"); }

function feltPoint(el) {
  const fr = feltEl().getBoundingClientRect();
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2 - fr.left, y: r.top + r.height / 2 - fr.top };
}

function potCenter() {
  const pot = document.querySelector("#table .pot");
  return pot ? feltPoint(pot) : { x: 0, y: 0 };
}

/* Fly one jeton across the felt along a slight arc, then remove it. */
function flyJeton(from, to, cls, delay, dur) {
  if (REDUCED_MOTION || !feltEl() || !from || !to) return;
  const el = document.createElement("i");
  el.className = "jeton fly " + cls;
  el.style.left = from.x + "px";
  el.style.top = from.y + "px";
  feltEl().appendChild(el);
  const dx = to.x - from.x, dy = to.y - from.y;
  const midX = dx * 0.5 + (Math.random() * 44 - 22);
  const midY = dy * 0.5 - 26 - Math.random() * 18;
  el.animate([
    { transform: "translate(-50%,-50%) scale(.9)", opacity: 0.9 },
    { transform: `translate(calc(-50% + ${midX}px), calc(-50% + ${midY}px)) scale(1.2)`,
      opacity: 1, offset: 0.55 },
    { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(.85)`,
      opacity: 0.9 },
  ], { duration: dur, delay, easing: "cubic-bezier(.3,.6,.4,1)", fill: "both" })
    .onfinish = () => el.remove();
}

/* Bet-chip positions currently on screen (read BEFORE the re-render). */
function betSpots() {
  if (!feltEl()) return [];
  return [...document.querySelectorAll("#opponents .bet-chip, #me .bet-chip")]
    .map((el) => ({
      pos: feltPoint(el),
      amt: parseInt(el.dataset.amt || "0", 10) || 1,
    }));
}

/* Street ended: everyone's bets slide into the pot. */
function flyBetsToPot(spots) {
  if (REDUCED_MOTION || !spots || !spots.length) return 0;
  const to = potCenter();
  spots.forEach((s, i) => {
    const n = Math.min(3, 1 + Math.floor(s.amt / Math.max(1, (state.view.big_blind || 10) * 5)));
    for (let k = 0; k < n; k++)
      flyJeton(s.pos, to, jetonClass(s.amt), i * 70 + k * 90, 520);
  });
  return 650;
}

/* ---------------- Pot-win celebration ---------------- */

function confettiBurst(delay, count) {
  if (REDUCED_MOTION || !feltEl()) return;
  const felt = feltEl();
  const fr = felt.getBoundingClientRect();
  const colors = ["#f5c451", "#e8e4da", "#c8403c", "#279e62", "#2f6fd0"];
  for (let i = 0; i < count; i++) {
    const b = document.createElement("i");
    b.className = "confetti-bit";
    b.style.background = colors[i % colors.length];
    b.style.left = (8 + Math.random() * 84) + "%";
    b.style.top = "-12px";
    felt.appendChild(b);
    b.animate([
      { transform: "translateY(0) rotate(0deg)", opacity: 1 },
      { transform: `translateY(${fr.height * 0.92}px) translateX(${Math.random() * 70 - 35}px)
                    rotate(${320 + Math.random() * 420}deg)`, opacity: 0 },
    ], {
      duration: 1400 + Math.random() * 900,
      delay: delay + Math.random() * 450,
      easing: "cubic-bezier(.3,.4,.6,1)",
      fill: "both",
    }).onfinish = () => b.remove();
  }
}

function celebrateWin(view, baseDelay) {
  const winners = new Map(view.result.winners.map((w) => [w.name, w.amount]));
  const meWon = view.players.some((p) => p.is_me && winners.has(p.name));
  const from = potCenter();
  const felt = feltEl();

  document.querySelectorAll("#table [data-name]").forEach((el) => {
    const name = el.dataset.name;
    if (!winners.has(name)) return;
    const to = feltPoint(el.querySelector(".plate") || el);

    // shower of jetons from the pot to the winner
    if (!REDUCED_MOTION) {
      for (let k = 0; k < 12; k++) {
        const jitter = { x: to.x + (Math.random() * 40 - 20), y: to.y + (Math.random() * 18 - 9) };
        const cls = JETON_DENOMS[Math.floor(Math.random() * JETON_DENOMS.length)][1];
        flyJeton(from, jitter, cls, baseDelay + k * 55, 720);
      }
    }

    // floating "+amount"
    if (felt) {
      const f = document.createElement("div");
      f.className = "win-float";
      f.textContent = "+" + fmt(winners.get(name));
      f.style.left = to.x + "px";
      f.style.top = (to.y - 26) + "px";
      f.style.animationDelay = (baseDelay + 350) + "ms";
      felt.appendChild(f);
      setTimeout(() => f.remove(), baseDelay + 2400);
    }
  });

  confettiBurst(baseDelay + 250, meWon ? 42 : 22);
  if (meWon && tg && tg.HapticFeedback)
    setTimeout(() => tg.HapticFeedback.notificationOccurred("success"), baseDelay + 300);
}

/* Spread opponents along the top arc of the oval table. */
function seatPos(i, n) {
  let deg;
  if (n === 1) deg = 90;                       // top center
  else deg = 196 - i * (212 / (n - 1));        // left rail → over the top → right rail
  const rad = (deg * Math.PI) / 180;
  return {
    x: 50 + 46 * Math.cos(rad),
    y: 40 - 34 * Math.sin(rad),
  };
}

function render(view) {
  if (!view) return;
  const prevView = state.view;
  state.view = view;
  // Sync our clock to the server so countdowns are accurate.
  state.clockSkew = Date.now() - view.server_time * 1000;
  // Diff on everything EXCEPT the per-second server clock, so the table only
  // re-renders on real changes (keeps the raise slider stable).
  const { server_time, ...rest } = view;
  const json = JSON.stringify(rest);
  if (json === state.lastJson) { tickCountdowns(); return; }
  state.lastJson = json;

  // Street over? Grab where the bet chips sit NOW, before the re-render
  // wipes them, so we can fly them into the pot afterwards.
  const streetCollected =
    prevView && prevView.code === view.code &&
    prevView.players.some((p) => p.round_bet > 0) &&
    view.players.every((p) => !p.round_bet) &&
    view.pot > 0 && view.stage !== "waiting";
  const oldBetSpots = streetCollected ? betSpots() : null;

  $("codeChip").textContent = view.code;
  $("stageLabel").textContent = view.stage;
  $("potAmt").textContent = fmt(view.pot);
  $("potJetons").innerHTML = jstackHtml(view.pot, 4);

  // Pulse the pot whenever it grows.
  const potBox = document.querySelector("#table .pot");
  if (state.prevPot != null && view.pot !== state.prevPot && view.pot > 0) {
    potBox.classList.remove("pop");
    void potBox.offsetWidth; // restart the animation
    potBox.classList.add("pop");
  }
  state.prevPot = view.pot;

  // Board — deal-animate only the newly revealed street, with a stagger.
  const prevLen = state.prevBoardLen || 0;
  const newFrom = view.board.length < prevLen ? 0 : prevLen;
  state.prevBoardLen = view.board.length;
  let b = view.board.map((c, i) =>
    i >= newFrom
      ? cardEl(c, "deal", `animation-delay:${((i - newFrom) * 0.12).toFixed(2)}s`)
      : cardEl(c)
  ).join("");
  for (let i = view.board.length; i < 5; i++) b += `<div class="empty-card"></div>`;
  $("board").innerHTML = b;

  // Who won this hand (used to glow the winning seats).
  state.winners = new Set(
    view.result && (view.stage === "hand_over" || view.stage === "showdown")
      ? view.result.winners.map((w) => w.name) : []
  );

  // Opponents
  const others = view.players.filter((p) => !p.is_me);
  $("opponents").innerHTML = others.length
    ? others.map((p, i) => seatHtml(p, i, others.length)).join("")
    : `<div class="wait-felt">Waiting for players…</div>`;

  // Me
  renderMe(view);

  // Result banner
  renderResult(view);

  // Controls
  renderControls(view);

  // Log
  $("log").innerHTML = (view.log || []).slice().reverse()
    .map((l) => `<li>${escapeHtml(l)}</li>`).join("");

  // Flight animations (need the freshly rendered DOM for positions).
  let delay = 0;
  if (oldBetSpots) delay = flyBetsToPot(oldBetSpots);
  const resultKey = view.result && (view.stage === "hand_over" || view.stage === "showdown")
    ? JSON.stringify(view.result.winners) : null;
  if (!view.result) state.lastCelebrated = null;
  if (resultKey && resultKey !== state.lastCelebrated) {
    state.lastCelebrated = resultKey;
    celebrateWin(view, delay);
  }
}

function seatHtml(p, i, n) {
  const cls = ["seat"];
  if (p.is_turn) cls.push("turn");
  if (p.folded) cls.push("folded");
  if (state.winners && state.winners.has(p.name)) cls.push("winner");
  let cards = "";
  if (p.cards)
    cards = p.cards.map((c, j) =>
      cardEl(c, "sm deal", `animation-delay:${(j * 0.12).toFixed(2)}s`)).join("");
  else if (!p.folded && state.view.stage !== "waiting" && state.view.stage !== "hand_over")
    cards = cardEl(null, "sm") + cardEl(null, "sm");
  let tag = "";
  if (p.folded) tag = `<span class="tag fold">folded</span>`;
  else if (p.all_in) tag = `<span class="tag allin">all-in</span>`;
  const dealer = p.is_dealer ? `<span class="badge">D</span>` : "";
  const bet = p.round_bet
    ? `<span class="bet-chip" data-amt="${p.round_bet}"><span class="jstack">${jstackHtml(p.round_bet, 3)}</span>${fmt(p.round_bet)}</span>` : "";
  const pos = seatPos(i, n);
  return `<div class="${cls.join(" ")}" data-name="${escapeHtml(p.name)}" style="left:${pos.x.toFixed(1)}%;top:${pos.y.toFixed(1)}%">
    <div class="mini-cards">${cards}</div>
    <div class="plate">${dealer}
      <div class="nm">${escapeHtml(p.name)}</div>
      <div class="ch">${fmt(p.chips)}</div>
    </div>
    ${bet}${tag}</div>`;
}

function renderMe(view) {
  const me = view.players.find((p) => p.is_me);
  const meEl = $("me");
  if (!me) { meEl.innerHTML = ""; return; }
  meEl.className = "me" + (me.is_turn ? " turn" : "")
    + (state.winners && state.winners.has(me.name) ? " winner" : "");
  // Fade the hole cards in only when a new hand is dealt to us.
  const myKey = view.my_cards.join("");
  const fresh = myKey && myKey !== state.prevMyKey;
  state.prevMyKey = myKey;
  const hand = view.my_cards.length
    ? view.my_cards.map((c, j) =>
        cardEl(c, "big" + (fresh ? " deal" : ""),
               fresh ? `animation-delay:${(j * 0.15).toFixed(2)}s` : "")).join("")
    : `<div class="empty-card"></div><div class="empty-card"></div>`;
  meEl.dataset.name = me.name;
  let bet = "";
  if (me.round_bet)
    bet = `<span class="bet-chip" data-amt="${me.round_bet}"><span class="jstack">${jstackHtml(me.round_bet, 3)}</span>${fmt(me.round_bet)}</span>`;
  else if (me.all_in)
    bet = `<span class="tag allin">all-in</span>`;
  const dealer = me.is_dealer ? `<span class="badge">D</span>` : "";
  meEl.innerHTML = `
    <div class="hand">${hand}</div>
    <div class="plate">${dealer}
      <span class="nm">${escapeHtml(me.name)}</span> ·
      <span class="ch">${fmt(me.chips)}</span>
    </div>
    <div>${bet}</div>`;
}

function renderResult(view) {
  const banner = $("resultBanner");
  if (view.result && (view.stage === "hand_over" || view.stage === "showdown")) {
    const wins = view.result.winners
      .map((w) => `<span class="win">${escapeHtml(w.name)}</span> +${fmt(w.amount)} (${escapeHtml(w.desc)})`)
      .join("<br>");
    banner.innerHTML = `<span class="trophy">🏆</span> ${wins}`;
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
      const label = view.stage === "hand_over" ? "Deal now" : "Deal hand";
      html += `<button class="deal" onclick="doStart()">🎬 ${label}</button>`;
    }
    html += `</div>`;
    if (view.can_rebuy) {
      html += `<button class="rebuy" onclick="doRebuy()">💰 Rebuy to ${view.buy_in}</button>`;
    }
    if (view.stage === "hand_over" && view.next_hand_at) {
      html += `<div class="wait">Next hand in <span class="countdown" data-deadline="${view.next_hand_at}">…</span>s</div>`;
    } else if (!view.can_start) {
      html += `<div class="wait">${view.is_host
        ? "Need 2+ players with chips to deal."
        : (view.seated ? "Waiting for the host to deal…" : "Spectating — rebuy to sit in.")}</div>`;
    }
    c.innerHTML = html;
    tickCountdowns();
    return;
  }

  // In a hand, not my turn — offer pre-actions + show whose turn.
  if (!view.my_turn || !view.actions) {
    let html = "";
    if (view.can_preact) {
      const m = view.my_preaction;
      html += `<div class="preact">
        <button class="${m === "check_fold" ? "on" : ""}" onclick="doPreact('check_fold')">Check / Fold</button>
        <button class="${m === "call_any" ? "on" : ""}" onclick="doPreact('call_any')">Call Any</button>
      </div>`;
    }
    const clock = view.turn_deadline
      ? ` <span class="countdown" data-deadline="${view.turn_deadline}">…</span>s` : "";
    html += `<div class="wait">${view.turn_name
      ? "Waiting for " + escapeHtml(view.turn_name) + "…" + clock : "…"}</div>`;
    c.innerHTML = html;
    tickCountdowns();
    return;
  }

  // My turn.
  const clock = view.turn_deadline
    ? `<div class="turnclock">⏱ <span class="countdown" data-deadline="${view.turn_deadline}">…</span>s</div>` : "";
  c.innerHTML = clock + actionsHtml(view.actions);
  wireRaise(view.actions);
  tickCountdowns();
}

function tickCountdowns() {
  const v = state.view;
  if (!v) return;
  const serverNow = (Date.now() - (state.clockSkew || 0)) / 1000;
  document.querySelectorAll(".countdown").forEach((el) => {
    const dl = parseFloat(el.dataset.deadline);
    const left = Math.max(0, Math.ceil(dl - serverNow));
    el.textContent = left;
    // Only the action timer goes urgent — not the "next hand" countdown.
    el.classList.toggle("low", left <= 5 && !!el.closest(".turnclock"));
  });
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
  if (a.can_call) row += `<button class="b-call" onclick="doAct('call')">Call ${fmt(a.call_amount)}</button>`;
  if (a.raise_min != null && a.raise_max > a.raise_min) {
    const verb = a.raise_verb === "bet" ? "Bet" : "Raise";
    row += `<button class="b-raise" onclick="doRaise()">${verb} to <span id="raiseBtnAmt">${state.raiseVal}</span></button>`;
  } else if (a.can_all_in) {
    row += `<button class="b-allin" onclick="doAct('all_in')">All-in ${fmt(a.all_in_amount)}</button>`;
  }
  row += `</div>`;
  // Always offer all-in as a secondary if raise row is shown.
  if (a.raise_min != null && a.raise_max > a.raise_min && a.can_all_in) {
    row += `<div class="act-row" style="margin-top:8px">
      <button class="b-allin" onclick="doAct('all_in')">All-in ${fmt(a.all_in_amount)}</button></div>`;
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
async function doPreact(mode) {
  const cur = state.view ? state.view.my_preaction : null;
  const send = cur === mode ? "none" : mode;
  const r = await api("preaction", { mode: send });
  if (r.error && !r.table) return flashStage(r.error.slice(0, 40));
  state.lastJson = ""; render(r.table || state.view);
}
async function doRebuy() {
  const r = await api("rebuy");
  if (r.error && !r.table) return flashStage(r.error.slice(0, 40));
  state.lastJson = ""; render(r.table);
}
window.invite = invite;
window.doAct = doAct;
window.doRaise = doRaise;
window.doStart = doStart;
window.doPreact = doPreact;
window.doRebuy = doRebuy;

// Smooth 1s countdown ticker (independent of the 1.5s state poll).
setInterval(tickCountdowns, 500);

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
