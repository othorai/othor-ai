/* SPDX-License-Identifier: Apache-2.0 */
// Small behaviours only: code boxes move focus and paste across; Copy copies the question.

// 0.29 · A table marked data-sort-table sorts by a click (or Enter) on a column with data-sort
// ("num" or "text"); each cell's data-v is its raw value. The server's order comes first; a
// second click reverses; aria-sort says which way. The footer row ("Others") stays last.
function sortTable(th) {
  const table = th.closest("table[data-sort-table]");
  if (!table || !table.tBodies[0]) return;
  const ths = [...th.parentElement.children];
  const i = ths.indexOf(th);
  const down = th.getAttribute("aria-sort") !== "descending";
  ths.forEach((x) => x.removeAttribute("aria-sort"));
  th.setAttribute("aria-sort", down ? "descending" : "ascending");
  const num = th.dataset.sort === "num";
  const val = (tr) => { const c = tr.children[i]; const v = c ? (c.dataset.v ?? c.textContent) : ""; return num ? parseFloat(v) || 0 : v.toLowerCase(); };
  const rows = [...table.tBodies[0].rows];
  rows.sort((a, b) => { const x = val(a), y = val(b); const d = num ? x - y : x.localeCompare(y); return down ? -d : d; });
  rows.forEach((r) => table.tBodies[0].appendChild(r));
}
document.addEventListener("click", (e) => {
  const th = e.target.closest("table[data-sort-table] th[data-sort]");
  if (th) sortTable(th);
});
document.addEventListener("keydown", (e) => {
  const th = e.target.closest && e.target.closest("table[data-sort-table] th[data-sort]");
  if (th && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); sortTable(th); }
});
document.addEventListener("input", (e) => {
  const box = e.target.closest("[data-code-boxes] .digit");
  if (box && box.value && box.nextElementSibling) box.nextElementSibling.focus();
});
document.addEventListener("paste", (e) => {
  const box = e.target.closest("[data-code-boxes] .digit");
  if (!box) return;
  const digits = (e.clipboardData.getData("text") || "").replace(/\D/g, "").slice(0, 6);
  if (!digits) return;
  e.preventDefault();
  const boxes = box.parentElement.querySelectorAll(".digit");
  digits.split("").forEach((d, i) => { if (boxes[i]) boxes[i].value = d; });
  (boxes[Math.min(digits.length, 5)] || box).focus();
});
document.addEventListener("keydown", (e) => {
  const box = e.target.closest("[data-code-boxes] .digit");
  if (!box) return;
  const group = box.parentElement;
  const all = [...group.querySelectorAll(".digit")];
  const digit = /^\d$/.test(e.key);
  // #41: after a wrong code the first key starts over: the old digits go, the typed one fills the
  // first box (never a mix of old and new digits, which the six-digit auto-submit would send)
  if (group.classList.contains("is-error") && (digit || e.key === "Backspace")) {
    e.preventDefault();
    group.classList.remove("is-error");
    all.forEach((b) => { b.value = ""; });
    if (digit) { all[0].value = e.key; (all[1] || all[0]).focus(); } else all[0].focus();
    return;
  }
  if (digit && box.value) { // typing into a filled box replaces its digit
    e.preventDefault();
    box.value = e.key;
    box.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  }
  if (e.key === "Backspace" && !box.value && box.previousElementSibling) { // one press a digit
    e.preventDefault();
    box.previousElementSibling.value = "";
    box.previousElementSibling.focus();
  }
});
// Every Copy button (one script): data-copy-text="…", data-copy="…" (or empty: the question
// beside it), data-copy-target="#id" (an element's text). The clipboard API, then a "Copied"
// toast; where the browser doesn't allow it, the text is selected for Ctrl+C / ⌘C.
function copyToast(text) {
  let dock = document.getElementById("toast-dock");
  if (!dock) {
    dock = document.createElement("div");
    dock.id = "toast-dock";
    dock.className = "toast-dock";
    dock.setAttribute("aria-live", "polite");
    document.body.appendChild(dock);
  }
  const t = document.createElement("div");
  t.className = "toast";
  t.setAttribute("role", "status");
  const dot = document.createElement("span");
  dot.className = "dot accent";
  const msg = document.createElement("span");
  msg.textContent = text;
  t.append(dot, msg);
  dock.replaceChildren(t);
  setTimeout(() => { if (t.isConnected) t.remove(); }, 2500);
}
function selectText(el) {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) { el.focus(); el.select(); return true; }
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  return true;
}
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-copy], [data-copy-text], [data-copy-target]");
  if (!btn) return;
  let text = btn.dataset.copyText || btn.dataset.copy || "";
  let el = btn.dataset.copyTarget ? document.querySelector(btn.dataset.copyTarget) : null;
  if (!el && !text) el = btn.closest(".ask")?.querySelector(".q");
  if (el && !text) text = el.innerText.trim();
  if (!text) return;
  const fallback = () => {
    const mac = /mac|iphone|ipad/i.test(navigator.userAgentData?.platform || navigator.platform || navigator.userAgent);
    if (selectText(el)) copyToast(`Selected · press ${mac ? "⌘C" : "Ctrl+C"} to copy`);
    else copyToast("Copy isn’t allowed here · select the text and copy it");
  };
  if (navigator.clipboard && window.isSecureContext !== false) {
    navigator.clipboard.writeText(text).then(() => copyToast("Copied"), fallback);
  } else {
    fallback();
  }
});
// "Resend code (0:42)": count down, then enable the resend button.
function tickCountdowns() {
  document.querySelectorAll("[data-countdown]").forEach((el) => {
    let s = parseInt(el.dataset.countdown, 10);
    if (isNaN(s) || s <= 0) return;
    s -= 1;
    el.dataset.countdown = String(s);
    el.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    if (s === 0) {
      const btn = el.closest("[data-resend]");
      if (btn) { btn.disabled = false; if (btn.classList.contains("link-muted")) btn.textContent = "Resend code"; }
    }
  });
}
setInterval(tickCountdowns, 1000);
// Six digits entered: submit the code form (the button stays for keyboards and screen readers).
// Not where the click is the decision (Delete company: data-no-autosubmit).
document.addEventListener("input", (e) => {
  const boxes = e.target.closest("[data-code-boxes]");
  if (!boxes || boxes.hasAttribute("data-no-autosubmit")) return;
  const all = [...boxes.querySelectorAll(".digit")];
  if (all.every((b) => /^\d$/.test(b.value))) boxes.closest("form")?.requestSubmit();
});
// 2kb: ticking an Ignored row shows the select bar with the count.
document.addEventListener("change", (e) => {
  if (!e.target.matches("[data-select-row]")) return;
  const form = e.target.closest("form");
  const n = form.querySelectorAll("[data-select-row]:checked").length;
  const bar = form.querySelector("[data-select-bar]");
  if (!bar) return;
  bar.hidden = n === 0;
  bar.querySelector("[data-selected-count]").textContent = `${n} selected`;
});
// 2kb · Clear (a reset button) unticks every row and hides the bar.
document.addEventListener("reset", (e) => {
  const bar = e.target.querySelector && e.target.querySelector("[data-select-bar]");
  if (bar) setTimeout(() => { bar.hidden = true; }, 0);
});
// Menus are Alpine components (M12, below); without JavaScript they are plain <details>.
// A select that applies on change (10c last row).
document.addEventListener("change", (e) => {
  if (e.target.matches("select[data-autosubmit]")) e.target.form?.requestSubmit();
});
// 2d: Copy the draft into the message; a "Seen in your files" chip adds itself to the input.
document.addEventListener("click", (e) => {
  const copy = e.target.closest("[data-copy-to]");
  if (copy) {
    const from = document.querySelector(copy.dataset.copyFrom);
    const to = document.querySelector(copy.dataset.copyTo);
    if (from && to) { to.value = from.innerText.trim(); to.focus(); }
    return;
  }
  const chip = e.target.closest("[data-append-to]");
  if (!chip) return;
  const input = document.querySelector(chip.dataset.appendTo);
  if (!input) return;
  const parts = input.value.split(",").map((x) => x.trim()).filter(Boolean);
  if (!parts.includes(chip.dataset.value)) parts.push(chip.dataset.value);
  input.value = parts.join(", ");
  chip.remove();
});
// 2b: choosing or dropping files submits the upload form.
document.addEventListener("change", (e) => {
  if (e.target.matches("[data-autosubmit]")) e.target.closest("form")?.requestSubmit();
});
// chips input: Enter or comma adds a chip, × removes one; the hidden field keeps "a, b".
function syncChips(box) {
  const vals = [...box.querySelectorAll("[data-chip]")].map((c) => c.dataset.chip);
  box.querySelector("[data-chips-value]").value = vals.join(", ");
}
document.addEventListener("click", (e) => {
  const x = e.target.closest("[data-chip-remove]");
  if (!x) return;
  const box = x.closest("[data-chips]");
  x.closest("[data-chip]").remove();
  syncChips(box);
});
document.addEventListener("keydown", (e) => {
  const input = e.target.closest("[data-chip-add]");
  if (!input || (e.key !== "Enter" && e.key !== ",")) return;
  e.preventDefault();
  const v = input.value.trim().replace(/,$/, "");
  if (!v) return;
  const box = input.closest("[data-chips]");
  if (![...box.querySelectorAll("[data-chip]")].some((c) => c.dataset.chip === v)) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.dataset.chip = v;
    chip.textContent = v;
    const x = document.createElement("button");
    x.type = "button"; x.className = "chip-x"; x.textContent = "×";
    x.setAttribute("aria-label", "Remove " + v); x.dataset.chipRemove = "";
    chip.appendChild(x);
    box.insertBefore(chip, input);
  }
  input.value = "";
  syncChips(box);
});

// Errors (M11a): an HTMX request that fails gets 9a's error toast from the server
// (X-Othor-Toast); it goes into the toast dock, and Retry sends the same request again.
function toastDock() {
  let dock = document.getElementById("toast-dock");
  if (!dock) {
    dock = document.createElement("div");
    dock.id = "toast-dock";
    dock.className = "toast-dock";
    dock.setAttribute("aria-live", "polite");
    document.body.appendChild(dock);
  }
  return dock;
}
document.addEventListener("htmx:beforeSwap", (e) => {
  const xhr = e.detail.xhr;
  if (!xhr || xhr.getResponseHeader("X-Othor-Toast") !== "1") return;
  e.detail.shouldSwap = true;
  e.detail.isError = false;
  e.detail.target = toastDock();
  e.detail.swapOverride = "innerHTML";
  const cfg = e.detail.requestConfig || {};
  toastDock()._retry = { verb: cfg.verb, path: cfg.path, elt: cfg.elt };
});
document.addEventListener("htmx:sendError", () => {
  const dock = toastDock();
  dock.innerHTML = "";
  const t = document.createElement("div");
  t.className = "toast err";
  t.setAttribute("role", "status");
  const dot = document.createElement("span");
  dot.className = "dot neg";
  const msg = document.createElement("span");
  msg.textContent = navigator.onLine ? "Couldn’t reach Othor AI" : "You’re offline.";
  t.append(dot, msg);
  dock.append(t);
});
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".toast .retry");
  if (!btn) return;
  const dock = btn.closest("#toast-dock");
  const r = dock && dock._retry;
  btn.closest(".toast").remove();
  if (r && r.verb && r.path && window.htmx) htmx.ajax(r.verb.toUpperCase(), r.path, { source: r.elt });
  else location.reload();
});
// An error toast never stands in the way (bug #68): a click on it (not on Retry) or anywhere
// else closes it, and the next request clears it; the click itself still goes through.
document.addEventListener("click", (e) => {
  const dock = document.getElementById("toast-dock");
  if (!dock || !dock.querySelector(".toast.err")) return;
  if (e.target.closest(".toast .retry, .toast a")) return;
  dock.querySelectorAll(".toast.err").forEach((t) => t.remove());
}, true);
document.addEventListener("htmx:beforeRequest", () => {
  const dock = document.getElementById("toast-dock");
  if (dock) dock.querySelectorAll(".toast.err").forEach((t) => t.remove());
});
// A live "N of M" beside a group of tick boxes: <span data-checked-count="NAME" data-of="M">
// counts the form's ticked inputs named NAME (Look back's "What to run", #69).
document.addEventListener("change", (e) => {
  const el = e.target;
  if (!(el instanceof HTMLInputElement) || el.type !== "checkbox" || !el.form || !el.name) return;
  const name = CSS.escape(el.name);
  el.form.querySelectorAll(`[data-checked-count="${name}"]`).forEach((c) => {
    const n = el.form.querySelectorAll(`input[name="${name}"]:checked`).length;
    c.textContent = `${n} of ${c.dataset.of}`;
  });
});
// Offline (9a): the callout while the browser has no network.
function offline() {
  const slot = document.getElementById("offline-slot");
  if (slot) slot.hidden = navigator.onLine;
}
window.addEventListener("online", offline);
window.addEventListener("offline", offline);
document.addEventListener("DOMContentLoaded", offline);
// A Look back report being made (#79, #80): the othor_lb_wait cookie names it. Any other page asks
// every 10 s whether it's ready; when it is, the note (its headline and Open) goes in the toast
// dock and the server clears the cookie. The report's own page shows it there instead.
document.addEventListener("DOMContentLoaded", () => {
  const m = document.cookie.match(/(?:^|; )othor_lb_wait=([0-9a-f-]{36})/);
  if (!m || location.pathname === "/look-back/" + m[1]) return;
  const timer = setInterval(async () => {
    try {
      const r = await fetch("/look-back/" + m[1] + "/ready", { credentials: "same-origin" });
      if (r.status === 204) return;
      clearInterval(timer);
      const html = r.ok ? (await r.text()).trim() : "";
      if (html) toastDock().innerHTML = html;
    } catch (_) { /* offline: the offline line says so */ }
  }, 10000);
});

// Stale page (9a): a page drawn from a run (data-run="<number>") checks once a minute whether
// a newer run has finished, and offers Reload.
document.addEventListener("DOMContentLoaded", () => {
  if (!document.querySelector("[data-run]")) return;
  const timer = setInterval(async () => {
    // read each time: Run again now redraws the page in place with its new run (v0.11)
    const el = document.querySelector("[data-run]");
    const seen = el ? el.dataset.run : null;
    if (!seen) return;
    try {
      const r = await fetch("/runs/latest", { method: "HEAD", credentials: "same-origin" });
      const now = r.headers.get("X-Othor-Run");
      if (r.ok && now && now !== seen) {
        clearInterval(timer);
        const slot = document.createElement("div");
        slot.className = "stale-slot";
        const box = document.createElement("div");
        box.className = "stale";
        box.setAttribute("role", "status");
        box.append("A newer run finished. ");
        const a = document.createElement("a");
        a.className = "link-u";
        a.href = "";
        a.textContent = "Reload";
        box.append(a);
        slot.append(box);
        document.body.append(slot);
      }
    } catch (_) { /* offline: the offline line says so */ }
  }, 60000);
});

// ---------------------------------------------------------------------------------------------
// M12 · Alpine components (the CSP build: templates name a component, never an expression).
// Menus, popovers and tab bars. Every one works without JavaScript: a menu is a <details>, a
// popover's text is in the page, a tab is a link.
document.addEventListener("alpine:init", () => {
  const Alpine = window.Alpine;
  // listbox (M11c): our own open list over a native <select>. The select stays the form value;
  // on a coarse pointer (phones) the native picker is kept.
  Alpine.data("listbox", () => ({
    init() {
      const wrap = this.$el;
      const sel = wrap.querySelector("select");
      if (!sel || window.matchMedia("(pointer: coarse)").matches) return;
      wrap.classList.add("lb-on");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = sel.className + " lb-button";
      btn.setAttribute("aria-haspopup", "listbox");
      btn.setAttribute("aria-expanded", "false");
      const lab = sel.id && document.querySelector(`label[for="${sel.id}"]`);
      if (lab) { lab.id = lab.id || `${sel.id}-l`; btn.setAttribute("aria-labelledby", `${lab.id} ${sel.id}-b`); lab.htmlFor = `${sel.id}-b`; }
      btn.id = `${sel.id}-b`;
      if (!lab && sel.getAttribute("aria-label")) btn.setAttribute("aria-label", sel.getAttribute("aria-label"));
      const list = document.createElement("div");
      list.className = "lb-menu";
      list.setAttribute("role", "listbox");
      list.id = `${sel.id}-list`;
      list.hidden = true;
      btn.setAttribute("aria-controls", list.id);
      const opts = [];
      const add = (o) => {
        const d = document.createElement("div");
        d.className = "lb-opt";
        d.setAttribute("role", "option");
        d.id = `${sel.id}-o${opts.length}`;
        const t = document.createElement("span");
        t.className = "lb-t";
        t.textContent = o.textContent;
        d.append(t);
        if (o.dataset.sub) { const s2 = document.createElement("span"); s2.className = "lb-sub"; s2.textContent = o.dataset.sub; t.append(s2); }
        d.addEventListener("mousedown", (e) => e.preventDefault());
        d.addEventListener("click", () => pick(opts.indexOf(d)));
        d.addEventListener("mousemove", () => move(opts.indexOf(d)));
        d._o = o;
        opts.push(d);
        list.append(d);
      };
      for (const ch of sel.children) {
        if (ch.tagName === "OPTGROUP") {
          const g = document.createElement("p");
          g.className = "lb-grp";
          g.textContent = ch.label;
          list.append(g);
          [...ch.children].forEach(add);
        } else add(ch);
      }
      if (wrap.dataset.note) { const n = document.createElement("p"); n.className = "lb-note"; n.textContent = wrap.dataset.note; list.append(n); }
      sel.after(btn, list);
      let active = -1;
      const label = () => {
        const o = sel.selectedOptions[0];
        btn.textContent = o ? o.textContent : "";
        opts.forEach((d) => { const on = d._o.selected; d.classList.toggle("is-on", on); d.setAttribute("aria-selected", on ? "true" : "false"); });
      };
      const move = (i) => {
        if (i < 0 || i >= opts.length) return;
        if (opts[active]) opts[active].classList.remove("is-active");
        active = i;
        opts[i].classList.add("is-active");
        btn.setAttribute("aria-activedescendant", opts[i].id);
        // scroll the list only: scrollIntoView also scrolled the page sideways (#18)
        const o = opts[i];
        if (o.offsetTop < list.scrollTop) list.scrollTop = o.offsetTop - 4;
        else if (o.offsetTop + o.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = o.offsetTop + o.offsetHeight - list.clientHeight + 4;
      };
      // The list sits on the window, under the button (#18): a card's overflow can't clip it; it
      // lines up with the button's right edge when there's no room on the right, and opens upward
      // when there's more room above. A scroll or resize outside it moves it with its button.
      const place = () => {
        const r = btn.getBoundingClientRect(), gap = 4, edge = 8;
        const w = Math.max(r.width, 200);
        list.style.width = `${w}px`;
        list.style.left = `${r.left + w > innerWidth - edge ? Math.max(edge, r.right - w) : r.left}px`;
        const below = innerHeight - r.bottom - gap - edge, above = r.top - gap - edge;
        const up = below < Math.min(320, list.scrollHeight) && above > below;
        list.style.maxHeight = `${Math.min(320, up ? above : below)}px`;
        list.style.top = up ? "" : `${r.bottom + gap}px`;
        list.style.bottom = up ? `${innerHeight - r.top + gap}px` : "";
      };
      const away = (e) => { if (e.target !== list && !list.contains(e.target)) place(); };
      const open = () => {
        list.hidden = false;
        place();
        addEventListener("scroll", away, true);
        addEventListener("resize", away);
        btn.setAttribute("aria-expanded", "true");
        btn.classList.add("is-open");
        move(Math.max(0, opts.findIndex((d) => d._o.selected)));
      };
      const close = (focus) => {
        list.hidden = true;
        removeEventListener("scroll", away, true);
        removeEventListener("resize", away);
        btn.setAttribute("aria-expanded", "false");
        btn.classList.remove("is-open");
        btn.removeAttribute("aria-activedescendant");
        if (focus) btn.focus();
      };
      const pick = (i) => {
        if (!opts[i]) return;
        if (!opts[i]._o.selected) {
          sel.value = opts[i]._o.value;
          sel.dispatchEvent(new Event("change", { bubbles: true }));
        }
        label();
        close(true);
      };
      let typed = "", timer;
      btn.addEventListener("click", () => (list.hidden ? open() : close(false)));
      btn.addEventListener("keydown", (e) => {
        const k = e.key;
        if (list.hidden) {
          if (["ArrowDown", "ArrowUp", "Enter", " "].includes(k)) { e.preventDefault(); open(); }
          return;
        }
        if (k === "ArrowDown") { e.preventDefault(); move(Math.min(active + 1, opts.length - 1)); }
        else if (k === "ArrowUp") { e.preventDefault(); move(Math.max(active - 1, 0)); }
        else if (k === "Home") { e.preventDefault(); move(0); }
        else if (k === "End") { e.preventDefault(); move(opts.length - 1); }
        else if (k === "Enter" || k === " ") { e.preventDefault(); pick(active); }
        else if (k === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
        else if (k === "Tab") close(false);
        else if (k.length === 1) {
          typed += k.toLowerCase();
          clearTimeout(timer);
          timer = setTimeout(() => { typed = ""; }, 600);
          const i = opts.findIndex((d) => d._o.textContent.toLowerCase().startsWith(typed));
          if (i >= 0) move(i);
        }
      });
      btn.addEventListener("blur", () => close(false));
      sel.addEventListener("change", label);
      label();
    },
  }));

  const menuItems = (d) => [...d.querySelectorAll("[role=menuitem], .menu a, .menu button")]
    .filter((x) => !x.disabled && x.offsetParent !== null);

  // menu: on a <details>. One open at a time; Escape and an outside click close it; arrow keys
  // move through the items; focus goes back to the trigger.
  // A menu near the foot of a clipping box (a table card scrolls sideways, so it clips) or of
  // the window opens upward instead of being cut off (M12b, 2ka/2ke).
  const flip = (d) => {
    const pop = d.querySelector(":scope > :not(summary)");
    if (!pop) return;
    d.classList.remove("opens-up");
    const box = pop.getBoundingClientRect();
    let limit = window.innerHeight;
    for (let p = d.parentElement; p; p = p.parentElement) {
      const o = getComputedStyle(p);
      if (/(auto|scroll|hidden|clip)/.test(o.overflowY + o.overflowX)) { limit = Math.min(limit, p.getBoundingClientRect().bottom); break; }
    }
    const room = d.getBoundingClientRect().top;
    if (box.bottom > limit && room > box.height) d.classList.add("opens-up");
  };
  Alpine.data("menu", () => ({
    init() {
      const d = this.$el;
      const trigger = d.querySelector(":scope > summary");
      d.dataset.menu = "";
      trigger.setAttribute("aria-haspopup", "menu");
      const sync = () => trigger.setAttribute("aria-expanded", d.open ? "true" : "false");
      sync();
      d.addEventListener("toggle", () => {
        sync();
        if (d.open) document.querySelectorAll("details[data-menu][open]").forEach((o) => { if (o !== d) o.open = false; });
        if (d.open) flip(d);
      });
      d.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && d.open) {
          e.stopPropagation();
          d.open = false;
          trigger.focus();
        } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          if (!d.open) { d.open = true; sync(); }
          const items = menuItems(d);
          if (!items.length) return;
          const i = items.indexOf(document.activeElement);
          const next = e.key === "ArrowDown" ? (i + 1) % items.length : (i <= 0 ? items.length - 1 : i - 1);
          items[next].focus();
        }
      });
      this._outside = (e) => { if (d.open && !d.contains(e.target)) d.open = false; };
      document.addEventListener("click", this._outside);
    },
    destroy() { document.removeEventListener("click", this._outside); },
  }));

  // popover: a button and the text beside it. Opens on click or keyboard focus, never on
  // hover alone; Escape or leaving closes it.
  // #48: a long Ontology line, cut with Read more (the CSP build takes names, not expressions)
  Alpine.data("readMore", () => ({
    more: false,
    get cut() { return !this.more; },
    get label() { return this.more ? "Show less" : "Read more"; },
    toggle() { this.more = !this.more; },
  }));
  Alpine.data("popover", () => ({
    init() {
      const btn = this.$el.querySelector("button");
      const body = this.$el.querySelector("[role=tooltip]");
      if (!btn || !body) return;
      const set = (on) => {
        body.hidden = !on;
        btn.setAttribute("aria-expanded", on ? "true" : "false");
        btn.classList.toggle("is-open", on);
      };
      set(false);
      // M15.7b: data-pop-hover also opens on hover where there is a mouse; a short delay on the
      // way out lets the pointer reach the card and its link. A click while hovering keeps it open.
      let hovered = false;
      let timer = 0;
      if (this.$el.hasAttribute("data-pop-hover") && window.matchMedia("(hover: hover)").matches) {
        this.$el.addEventListener("mouseenter", () => { clearTimeout(timer); hovered = true; set(true); });
        this.$el.addEventListener("mouseleave", () => {
          hovered = false;
          timer = setTimeout(() => { if (!this.$el.contains(document.activeElement)) set(false); }, 150);
        });
      }
      btn.addEventListener("click", () => set(hovered ? true : body.hidden));
      body.querySelectorAll("[data-pop-close]").forEach((x) => x.addEventListener("click", () => { set(false); btn.focus(); }));
      btn.addEventListener("focus", () => { if (btn.matches(":focus-visible")) set(true); });
      this.$el.addEventListener("focusout", (e) => { if (!this.$el.contains(e.relatedTarget)) set(false); });
      this.$el.addEventListener("keydown", (e) => { if (e.key === "Escape" && !body.hidden) { e.stopPropagation(); set(false); btn.focus(); } });
      document.addEventListener("click", (e) => { if (!this.$el.contains(e.target)) set(false); });
    },
  }));

  // tabs: a bar of links. Left/Right, Home and End move between them.
  Alpine.data("tabs", () => ({
    init() {
      const bar = this.$el;
      bar.addEventListener("keydown", (e) => {
        const tabs = [...bar.querySelectorAll(".tab")];
        const i = tabs.indexOf(document.activeElement);
        if (i < 0) return;
        const to = { ArrowRight: (i + 1) % tabs.length, ArrowLeft: (i - 1 + tabs.length) % tabs.length, Home: 0, End: tabs.length - 1 }[e.key];
        if (to === undefined) return;
        e.preventDefault();
        tabs[to].focus();
      });
    },
  }));
});

// ---------------------------------------------------------------------------------------------
// M12 · Panels open and close without a reload. A link that changes the panel on the same page
// (a ?panel= link, a finding, a tab or step inside a panel, the close link) fetches the page,
// swaps #main and #overlay, and pushes the URL. The panel slides in when it opens and out when
// it closes; a direct load (refresh, shared link) shows it with no slide. Anything else, or any
// failure, is an ordinary navigation.
const REDUCED = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const OPEN_PANEL = ".panel.wide:not(.in-page), .panel.chat:not(.in-page)";
const seg = (path) => path.split("/")[1] || "";
const isPanelUrl = (u) => u.searchParams.has("panel") || u.pathname.includes("/finding/");

function panelLink(a, e) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return false;
  if (!a.href || a.target || a.hasAttribute("download") || a.hasAttribute("data-no-panel")) return false;
  if (!window.fetch || !window.DOMParser || !document.getElementById("main")) return false;
  const u = new URL(a.href, location.href);
  const here = new URL(location.href);
  if (u.origin !== here.origin || seg(u.pathname) !== seg(here.pathname)) return false;
  if (u.pathname === here.pathname && u.search === here.search && u.hash) return false;  // an anchor
  return a.hasAttribute("data-panel") || isPanelUrl(u) || isPanelUrl(here) || !!a.closest(OPEN_PANEL + ", .scrim");
}

function finish(el, cls) {
  return new Promise((done) => {
    if (!el || REDUCED()) return done();
    let over = false;
    const end = () => { if (!over) { over = true; el.classList.remove(cls); done(); } };
    el.addEventListener("animationend", end, { once: true });
    setTimeout(end, 400);
    el.classList.add(cls);
  });
}

let panelOpener = null;
async function panelGo(url, push) {
  let r;
  try {
    r = await fetch(url, { credentials: "same-origin", headers: { "X-Othor-Panel": "1" } });
  } catch (_) { location.href = url; return; }
  const landed = new URL(r.url || url, location.href);
  if (!r.ok || seg(landed.pathname) !== seg(location.pathname)) { location.href = url; return; }
  const doc = new DOMParser().parseFromString(await r.text(), "text/html");
  const main = doc.getElementById("main");
  if (!main) { location.href = url; return; }
  const before = document.querySelector(OPEN_PANEL);
  const after = doc.querySelector(OPEN_PANEL);
  if (before && !after) {
    await Promise.all([finish(before, "is-leaving"), finish(document.querySelector(".scrim"), "is-leaving")]);
  }
  const y = window.scrollY;
  document.getElementById("main").replaceWith(main);
  const ov = document.getElementById("overlay");
  const newOv = doc.getElementById("overlay");
  if (ov && newOv) ov.replaceWith(newOv);
  const fold = document.getElementById("fold");
  const newFold = doc.getElementById("fold");
  if (fold && newFold) fold.replaceWith(newFold);
  if (doc.title) document.title = doc.title;
  if (push) history.pushState({ othorPanel: true }, "", landed.pathname + landed.search + landed.hash);
  window.scrollTo(0, y);
  if (window.htmx) { htmx.process(main); if (newOv) htmx.process(newOv); }
  const panel = document.querySelector(OPEN_PANEL);
  if (panel && !before) {
    finish(panel, "is-entering");
    finish(document.querySelector(".scrim"), "is-entering");
  }
  if (panel) {
    if (!panel.contains(document.activeElement)) {
      panel.setAttribute("tabindex", "-1");
      (panel.querySelector("[autofocus]") || panel).focus({ preventScroll: true });
    }
  } else if (panelOpener) {
    const back = document.querySelector(`#main a[href="${CSS.escape(panelOpener)}"]`);
    if (back) back.focus({ preventScroll: true });
    panelOpener = null;
  }
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  // a link that opens a dialog (M15.3: Add to a brief) keeps its href only as the no-JS fallback
  if (!a || a.hasAttribute("data-dialog-open") || !panelLink(a, e)) return;
  e.preventDefault();
  if (!document.querySelector(OPEN_PANEL)) panelOpener = a.getAttribute("href");
  panelGo(a.href, true);
});
window.addEventListener("popstate", (e) => {
  if (e.state && e.state.othorPanel) panelGo(location.href, false);
});
document.addEventListener("DOMContentLoaded", () => {
  if (!history.state) history.replaceState({ othorPanel: true }, "");
});

// Escape closes the open panel (its close link); Tab stays inside the 680 px panel, which is
// modal (the scrim); the 480 px chat leaves the page live.
document.addEventListener("keydown", (e) => {
  const panel = document.querySelector(OPEN_PANEL);
  if (!panel) return;
  if (e.key === "Escape" && !e.defaultPrevented) {
    const close = panel.querySelector('a[aria-label="Close"][href]') || document.querySelector('a.scrim[href]');
    if (close) { e.preventDefault(); close.click(); }
    return;
  }
  if (e.key !== "Tab" || !panel.classList.contains("wide")) return;
  const items = [...panel.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter((x) => x.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (!panel.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

// ---------------------------------------------------------------------------------------------
// M12b · Actions happen in place (docs/07). A form marked data-inplace (the action_form macro)
// posts with fetch, follows the server's redirect, and swaps #main, #overlay and the website's top
// fold (#fold, where an inner page's heading sits) from the page it
// lands on: nothing reloads, the scroll position stays, every count updates with it. The row the
// action came from fades out first; focus goes to the row now in its place (or the next one).
// A failure comes back as the 9a toast (X-Othor-Toast); landing in another area is an ordinary
// navigation. Without JavaScript the form is the same plain post.
const ROW = "[data-row]";

function rowSpot(form) {
  const row = form.closest(ROW);
  const list = row && row.closest("[data-rows]");
  if (!row || !list) return null;
  const rows = [...list.querySelectorAll(ROW)];
  const label = list.closest("[aria-label]");
  return { row, index: rows.indexOf(row), list: label ? label.getAttribute("aria-label") : null };
}

// A form marked data-keep-focus (Summary's controls, M16.3) keeps focus on the control the owner
// used, found again by its name (and value, for a radio) after the redraw.
function keepFocus(e) {
  const f = e.target.form;
  if (f && f.hasAttribute("data-keep-focus") && e.target.name) f._lastFocus = [e.target.name, e.target.value, e.target.type];
}
document.addEventListener("focusin", keepFocus);
document.addEventListener("change", keepFocus, true);

function focusSpot(spot, form) {
  let target = null;
  if (form._lastFocus) {
    const [name, value, type] = form._lastFocus;
    const sel = `form[action="${CSS.escape(form.getAttribute("action") || "")}"] [name="${CSS.escape(name)}"]` +
      (type === "radio" ? `[value="${CSS.escape(value)}"]` : "");
    target = document.querySelector(sel);
  }
  if (!target && spot) {
    const scope = spot.list ? document.querySelector(`[aria-label="${CSS.escape(spot.list)}"]`) : document;
    const rows = scope ? [...scope.querySelectorAll(ROW)] : [];
    const row = rows[Math.min(spot.index, rows.length - 1)];
    if (row) target = row.querySelector("button:not([disabled]), a[href], input:not([type=hidden])");
  }
  if (!target && form.closest("#overlay")) {
    const panel = document.querySelector(OPEN_PANEL);
    target = panel && panel.querySelector("button:not([disabled]), input:not([type=hidden]):not([disabled])");
  }
  if (!target) {
    target = document.querySelector("#main h1") || document.querySelector("#fold h1");
    if (target) target.setAttribute("tabindex", "-1");
  }
  if (target) target.focus({ preventScroll: true });
}

// Picks the owner made but hasn't saved (a radio in another form on the page) survive the redraw
// an in-place action brings (#20: pick an answer, Set aside another question, the pick was lost).
// Only a group the server left unanswered gets its pick back: what the server says still wins.
function unsavedPicks(form) {
  const picks = [];
  for (const scope of [document.getElementById("main"), document.getElementById("overlay")]) {
    if (!scope) continue;
    scope.querySelectorAll('input[type="radio"]:checked').forEach((el) => {
      if (el.name && el.form !== form && !el.defaultChecked) picks.push([el.name, el.value]);
    });
  }
  return picks;
}

function restorePicks(picks) {
  for (const [name, value] of picks) {
    const group = [...document.querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`)];
    if (!group.length || group.some((el) => el.checked)) continue;
    const el = group.find((x) => x.value === value);
    if (el) { el.checked = true; el.dispatchEvent(new Event("change", { bubbles: true })); }
  }
}

async function inplaceSubmit(form, submitter) {
  const spot = rowSpot(form);
  const picks = unsavedPicks(form);
  const data = new FormData(form, submitter);
  // a file upload keeps its multipart body; everything else posts as a plain form
  const body = form.enctype === "multipart/form-data" ? data : new URLSearchParams(data);
  form.setAttribute("aria-busy", "true");
  if (submitter) submitter.disabled = true;
  const leaving = spot && !REDUCED() ? finish(spot.row, "is-leaving") : Promise.resolve();
  // a button with its own formaction (New brief's Create) posts there, as the plain form would
  // the attribute, never `form.action`: a field named "action" (Edit brief's buttons) shadows it
  const own = new URL(form.getAttribute("action") || location.href, location.href).href;
  const action = submitter && submitter.hasAttribute("formaction") ? submitter.formAction : own;
  let r;
  try {
    r = await fetch(action, {
      method: "POST",
      body,
      credentials: "same-origin",
      headers: { "X-Othor-Inplace": "1" },
    });
  } catch (_) {
    form.removeAttribute("aria-busy");
    if (submitter) submitter.disabled = false;
    if (spot) spot.row.classList.remove("is-leaving");
    busyEnd(form);
    document.dispatchEvent(new CustomEvent("htmx:sendError"));
    return;
  }
  const text = await r.text();
  if (r.headers.get("X-Othor-Toast") === "1") {
    form.removeAttribute("aria-busy");
    if (submitter) submitter.disabled = false;
    if (spot) spot.row.classList.remove("is-leaving");
    busyEnd(form);
    toastDock().innerHTML = text;
    return;
  }
  const landed = new URL(r.url || action, location.href);
  const doc = new DOMParser().parseFromString(text, "text/html");
  const main = doc.getElementById("main");
  if (!r.ok || !main || seg(landed.pathname) !== seg(location.pathname)) {
    location.href = landed.href;
    return;
  }
  await leaving;
  const y = window.scrollY;
  const was = document.getElementById("main");
  const tall = was.offsetHeight;
  was.replaceWith(main);
  // a row that comes back a few px shorter (Undo in its place) must not pull a page scrolled to
  // its end up: the page keeps its height until the next swap
  if (main.offsetHeight < tall) main.style.minHeight = `${tall}px`;
  const ov = document.getElementById("overlay");
  const newOv = doc.getElementById("overlay");
  if (ov && newOv) ov.replaceWith(newOv);
  const fold = document.getElementById("fold");
  const newFold = doc.getElementById("fold");
  if (fold && newFold) fold.replaceWith(newFold);
  // the top bar's actions (v0.11: Run again now waits there while its run runs)
  const acts = document.getElementById("app-actions");
  const newActs = doc.getElementById("app-actions");
  if (acts && newActs) acts.replaceWith(newActs);
  if (document.contains(form)) busyEnd(form);
  if (doc.title) document.title = doc.title;
  // another page of the same area (a new brief's edit page, a replay): its address goes in the
  // history; the same page keeps its address (a refresh shows it without the one-off note)
  if (landed.pathname !== location.pathname) {
    history.pushState({ othorPanel: true }, "", landed.pathname + landed.search);
  }
  window.scrollTo(0, y);
  if (window.htmx) { htmx.process(main); if (newOv) htmx.process(newOv); }
  restorePicks(picks);
  focusSpot(spot, form);
}

document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-inplace") || e.defaultPrevented) return;
  if (!window.fetch || !window.DOMParser || !document.getElementById("main")) return;
  if (form.getAttribute("aria-busy") === "true") { e.preventDefault(); return; }
  e.preventDefault();
  inplaceSubmit(form, e.submitter || null);
});

// ---------------------------------------------------------------------------------------------
// v0.11 · A form that waits on Othor AI says so in place (#27: New brief showed nothing while it
// drafted). The submit button with data-busy shows that label ("Drafting…"); the form's
// [data-busy-line] shows the button's data-busy-say, or the form's for any other submit (the
// Change anything line); every button in the form waits. The redraw that comes back replaces
// it all; a failed in-place post puts it back (busyEnd), and so does coming Back to the page.
function busyStart(form, submitter) {
  const own = submitter && submitter.dataset.busy ? submitter : null;
  const say = own ? own.dataset.busySay : form.dataset.busySay;
  if (!own && !say) return;
  if (form._busy) return;
  const was = [];
  form._busy = { own, label: own ? own.textContent : "", was };
  if (own) own.textContent = own.dataset.busy;
  const line = form.querySelector("[data-busy-line]");
  if (line && say) {
    const t = line.querySelector("[data-busy-text]");
    if (t) t.textContent = say;
    line.hidden = false;
  }
  // after the form's data is taken (a disabled button would drop out of it)
  setTimeout(() => {
    if (!form._busy || form._busy.was !== was) return;
    form.querySelectorAll("button").forEach((b) => { if (!b.disabled) { b.disabled = true; was.push(b); } });
  }, 0);
}

function busyEnd(form) {
  const b = form._busy;
  if (!b) return;
  form._busy = null;
  if (b.own) b.own.textContent = b.label;
  b.was.forEach((x) => { x.disabled = false; });
  const line = form.querySelector("[data-busy-line]");
  if (line) line.hidden = true;
}

document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement) || form.getAttribute("aria-busy") === "true") return;
  busyStart(form, e.submitter || null);
}, true);
window.addEventListener("pageshow", (e) => {
  if (e.persisted) document.querySelectorAll("form").forEach(busyEnd);
});

// ---------------------------------------------------------------------------------------------
// M12b · A live poll (hx-trigger "every …": the scan, the Sheets check, drafting) waits while
// the owner is doing something inside it: a menu open, a row ticked, a field focused or typed
// in. Swapping the region would throw that away. The poll resumes on its next tick once they're
// done.
function busyIn(region) {
  if (region.querySelector("details[open]")) return true;
  // a box the owner changed (one the server rendered ticked doesn't hold the poll)
  if ([...region.querySelectorAll('input[type="checkbox"]:not([disabled])')].some((x) => x.checked !== x.defaultChecked)) return true;
  const a = document.activeElement;
  if (a && region.contains(a) && a.matches("input:not([type=hidden]), textarea, select")) return true;
  return [...region.querySelectorAll('input[type="search"], input[name="q"], textarea')].some((x) => x.value && x !== a && x.defaultValue !== x.value);
}
document.addEventListener("htmx:beforeRequest", (e) => {
  const elt = e.detail && e.detail.elt;
  if (!elt || !elt.getAttribute) return;
  const trigger = elt.getAttribute("hx-trigger") || "";
  if (!/\bevery\b/.test(trigger)) return;
  if (busyIn(elt)) e.preventDefault();
});

// ---------------------------------------------------------------------------------------------
// M11c · Dialog: any button with data-dialog-open="<id>" opens that <dialog class="dlg"> as a
// modal (the browser keeps focus inside and closes it on Escape); a click on the backdrop closes
// it; focus returns to the button.
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-dialog-open]");
  if (t) {
    const d = document.getElementById(t.dataset.dialogOpen);
    if (d && typeof d.showModal === "function") {
      e.preventDefault();
      d._from = t;
      d.showModal();
    }
    return;
  }
  if (e.target.matches && e.target.matches("dialog.dlg[open]")) e.target.close();  // the backdrop
});
document.addEventListener("close", (e) => {
  if (e.target.matches && e.target.matches("dialog.dlg") && e.target._from) e.target._from.focus();
}, true);

// ---------------------------------------------------------------------------------------------
// v0.11 · Ontology, Tell Othor AI: an example chip (data-tell-fill) fills the input and focuses it,
// with the caret at the end; the owner edits it or presses Enter. Nothing is sent by the chip.
// New brief uses the same: a suggested question fills the form's [data-fill-target] box (and its
// `suggestion` field), a draft row's Change starts the line in the form's Change anything box.
document.addEventListener("click", (e) => {
  const chip = e.target.closest && e.target.closest("[data-tell-fill]");
  if (!chip) return;
  const box = chip.closest(".tell");
  const form = chip.closest("form");
  const input = (box && box.querySelector('input[name="line"], textarea[name="line"]'))
    || (form && form.querySelector('[data-fill-target], .tell input[name="line"]'));
  if (!input) return;
  e.preventDefault();
  const sug = form && "suggestion" in chip.dataset && form.querySelector('input[name="suggestion"]');
  if (sug) sug.value = chip.dataset.suggestion;
  input.value = chip.dataset.tellFill;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
});

// ---------------------------------------------------------------------------------------------
// M11c · Company step (2a): the settings line. The time zone is taken from the browser when it is
// on the list (else India stays, and the line says so); the line follows the fields as they change.
document.addEventListener("DOMContentLoaded", () => {
  const line = document.querySelector("[data-settings-line]");
  if (!line) return;
  const fy = line.querySelector('select[name="fy"]');
  const tz = line.querySelector("select[data-tz-detect]");
  const cur = line.querySelector('select[name="currency"]');  // M15.6: ₹ or $
  const note = line.querySelector('[data-sl="note"]');
  const text = (sel) => (sel && sel.selectedOptions[0] ? sel.selectedOptions[0].textContent.trim() : "");
  const sync = () => {
    const f = line.querySelector('[data-sl="fy"]');
    const t = line.querySelector('[data-sl="tz"]');
    if (f && fy) f.textContent = text(fy);
    if (t && tz) t.textContent = text(tz);
    const c = line.querySelector('[data-sl="cur"]');
    if (c && cur) {
      let lines = {};
      try { lines = JSON.parse(c.dataset.lines || "{}"); } catch (e) { lines = {}; }
      if (lines[cur.value]) c.textContent = lines[cur.value];
    }
  };
  if (tz) {
    let zone = "";
    try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { zone = ""; }
    const hit = [...tz.options].find((o) => o.value === zone);
    if (hit && tz.value !== zone) {
      tz.value = zone;
      tz.dispatchEvent(new Event("change", { bubbles: true }));
    }
    if (note) {
      note.textContent = hit ? "Time zone taken from your browser." : "We couldn’t detect your time zone, so India time is set. Change it if that’s wrong.";
      note.hidden = false;
    }
  }
  [fy, tz, cur].forEach((s) => s && s.addEventListener("change", sync));
  sync();
});

// ---------------------------------------------------------------------------------------------
// M11c · Panels carry on in place.
// - An answer that holds <span data-panel-go="url"> (Connect's passing test, a scan that just
//   finished) moves the panel there, as a panel link would: no reload.
// - A search (a GET form) inside an open panel loads its results into the panel.
// - An htmx action inside an open panel that answers HX-Redirect (a table's use, its Undo)
//   updates the page in place instead of reloading it.
function goFrom(root) {
  const t = root && root.querySelector && root.querySelector("[data-panel-go]");
  if (!t) return;
  const url = t.getAttribute("data-panel-go");
  t.remove();
  const u = new URL(url, location.href);
  if (document.getElementById("main") && seg(u.pathname) === seg(location.pathname)) panelGo(u.href, true);
  else location.href = u.href;
}
document.addEventListener("htmx:afterSettle", () => goFrom(document));

// #96: a toast says its line and goes: 6 s, or 12 s when it offers Undo or another action; held
// while the pointer or focus is on it. An error toast (Retry) stays. The page's "?created=" that
// brought a toast leaves the address at once, so Back or a reload doesn't show it again.
function timeToasts(root) {
  root.querySelectorAll(".toast-dock .toast:not(.err)").forEach((t) => {
    if (t._timed) return;
    t._timed = true;
    let left = t.querySelector("form, .action, button") ? 12000 : 6000;
    let since = Date.now();
    let id = setTimeout(() => t.remove(), left);
    const hold = () => { clearTimeout(id); left -= Date.now() - since; };
    const go = () => {
      if (t.matches(":hover") || t.contains(document.activeElement)) return;
      since = Date.now();
      id = setTimeout(() => t.remove(), Math.max(left, 2000));
    };
    t.addEventListener("pointerenter", hold);
    t.addEventListener("focusin", hold);
    t.addEventListener("pointerleave", go);
    t.addEventListener("focusout", go);
  });
}
function dropToastParam() {
  const u = new URL(location.href);
  if (!u.searchParams.has("created")) return;
  u.searchParams.delete("created");
  history.replaceState(history.state, "", u.pathname + (u.search || "") + u.hash);
}
document.addEventListener("DOMContentLoaded", () => { timeToasts(document); dropToastParam(); });
document.addEventListener("htmx:afterSettle", () => { timeToasts(document); dropToastParam(); });
document.addEventListener("DOMContentLoaded", () => goFrom(document));

document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement) || e.defaultPrevented) return;
  if ((form.getAttribute("method") || "get").toLowerCase() !== "get" || !form.closest(OPEN_PANEL)) return;
  if (!window.fetch || !document.getElementById("main")) return;
  e.preventDefault();
  const u = new URL(form.getAttribute("action") || location.href, location.href);
  u.search = new URLSearchParams(new FormData(form)).toString();
  panelGo(u.href, true);
});

document.addEventListener("htmx:beforeOnLoad", (e) => {
  const xhr = e.detail && e.detail.xhr;
  const elt = e.detail && e.detail.elt;
  const to = xhr && xhr.getResponseHeader("HX-Redirect");
  if (!to || !elt || !elt.closest || !elt.closest(OPEN_PANEL)) return;
  const u = new URL(to, location.href);
  if (!document.getElementById("main") || seg(u.pathname) !== seg(location.pathname)) return;
  e.preventDefault();
  panelGo(u.href, false);
});

// The website's sticky nav (owner, 10 Nov 2026): clear over the page's first fold, dark once the
// page has scrolled. A class only; without JavaScript the bar stays as it is at the top.
(function () {
  const bar = document.getElementById("site-bar");
  if (!bar) return;
  const update = () => bar.classList.toggle("is-stuck", window.scrollY > 8);
  update();
  window.addEventListener("scroll", update, { passive: true });
})();

// The home page's intro video (owner, 19 Nov 2026): the play button starts it in place with the
// native controls and sound; at the end it goes back to the poster and the button. Without
// JavaScript the button does nothing and the poster stays.
document.querySelectorAll("[data-site-video]").forEach((box) => {
  const v = box.querySelector("video");
  const play = box.querySelector("button");
  if (!v || !play) return;
  play.addEventListener("click", () => {
    box.classList.add("is-playing");
    v.controls = true;
    v.play().catch(() => {});
    v.focus();
  });
  v.addEventListener("ended", () => {
    box.classList.remove("is-playing");
    v.controls = false;
    v.load(); // the poster again
    play.focus();
  });
});

// The website's part links (owner's design, 11 Nov 2026): "On this page" on the legal pages and
// the numbered tabs on /product and /use-cases mark the part in view. Without JavaScript the
// links still jump; only the mark is missing.
(function () {
  const lists = document.querySelectorAll(".site-legal-toc, .site-tabs");
  if (!lists.length || !("IntersectionObserver" in window)) return;
  lists.forEach((list) => {
    const links = [...list.querySelectorAll('a[href^="#"]')];
    const parts = links.map((a) => document.getElementById(a.getAttribute("href").slice(1))).filter(Boolean);
    if (!parts.length) return;
    const mark = (id) => links.forEach((a) => {
      if (a.getAttribute("href") === "#" + id) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
    const seen = new Set();
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => (e.isIntersecting ? seen.add(e.target) : seen.delete(e.target)));
      const first = parts.find((p) => seen.has(p));
      if (first) mark(first.id);
    }, { rootMargin: "-140px 0px -55% 0px" });
    parts.forEach((p) => io.observe(p));
  });
})();

// M15.7 · a dialog's download form (Download PDF): the browser downloads the file; the dialog
// closes once the request is on its way, and the page stays as it was
document.addEventListener("submit", (e) => {
  const f = e.target;
  if (!f.matches || !f.matches("form[data-dialog-submit]")) return;
  const d = f.closest("dialog");
  if (d) setTimeout(() => d.close(), 0);
});

// #63 (owner, 14 Nov 2026): a menu click shows the new page's menu item as current at once and
// the main panel's loading view (#main-skeleton) until the page arrives. A modified click (new
// tab) is left alone; coming Back to this page puts its content back.
(function () {
  let saved = null;
  document.addEventListener("click", (e) => {
    const a = e.target.closest(".side a.nav-item");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target && a.target !== "_self") return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || (url.pathname === location.pathname && url.search === location.search)) return;
    document.querySelectorAll(".side .is-on").forEach((x) => { x.classList.remove("is-on"); x.removeAttribute("aria-current"); });
    a.classList.add("is-on");
    a.setAttribute("aria-current", "page");
    const main = document.getElementById("main");
    const tpl = document.getElementById("main-skeleton");
    if (main && tpl) {
      saved = main.innerHTML;
      main.replaceChildren(tpl.content.cloneNode(true));
    }
  });
  window.addEventListener("pageshow", (e) => {
    if (e.persisted && saved !== null) { location.reload(); }
  });
})();

// 0.28.4 (owner, 19 Nov 2026): the Othor AI chat opens at its end, the latest answer just above
// the reply box, however it arrives (a direct load, the panel sliding in, a reply swapped in)
(function () {
  const toEnd = (root) => {
    const els = root.matches && root.matches("[data-at-end]") ? [root] : root.querySelectorAll ? root.querySelectorAll("[data-at-end]") : [];
    els.forEach((el) => { el.scrollTop = el.scrollHeight; });
  };
  const start = () => {
    toEnd(document);
    new MutationObserver((list) => {
      list.forEach((m) => m.addedNodes.forEach((n) => { if (n.nodeType === 1) toEnd(n); }));
    }).observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
