/* VGG OS page behaviour. Loaded with defer; no inline script or handler attributes anywhere (CSP script-src 'self'). */
(function () {
  "use strict";
  var root = document.documentElement;
  var KEY = "vggos.nav.collapsed";

  function store(get, value) {
    try {
      if (get) return window.localStorage.getItem(KEY);
      window.localStorage.setItem(KEY, value);
    } catch (e) { return null; }
    return null;
  }

  /* Sidebar collapse (desktop). Preference only; no data. */
  var collapseBtns = document.querySelectorAll("[data-collapse]");
  function applyCollapsed(on) {
    root.classList.toggle("nav-collapsed", on);
    collapseBtns.forEach(function (btn) {
      btn.setAttribute("aria-expanded", on ? "false" : "true");
      btn.setAttribute("aria-label", on ? "Expand navigation" : "Collapse navigation");
      var label = btn.querySelector(".tool-label");
      if (label) label.textContent = on ? "Expand" : "Collapse";
    });
  }
  applyCollapsed(store(true) === "1");
  collapseBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var on = !root.classList.contains("nav-collapsed");
      applyCollapsed(on);
      store(false, on ? "1" : "0");
    });
  });

  /* Theme choice lives in Settings. Sync radios after paint. */
  if (window.vggosTheme && typeof window.vggosTheme.apply === "function") {
    window.vggosTheme.apply();
  }
  document.addEventListener("change", function (event) {
    var input = event.target;
    if (!(input && input.matches && input.matches('input[name="theme"][data-theme-choice]'))) return;
    if (window.vggosTheme && typeof window.vggosTheme.set === "function") {
      window.vggosTheme.set(input.value);
    }
  });

  /* Slim progress bar for full page navigations and form posts. */
  var bar = document.getElementById("progress");
  function startProgress() {
    if (!bar) return;
    bar.classList.remove("is-done");
    void bar.offsetWidth;
    bar.classList.add("is-active");
  }
  function stopProgress() {
    if (!bar) return;
    bar.classList.remove("is-active");
    bar.classList.add("is-done");
  }
  window.addEventListener("pageshow", stopProgress);

  function isPlainNavigation(link, event) {
    if (event.defaultPrevented || event.button !== 0) return false;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
    if (link.target && link.target !== "_self") return false;
    if (link.hasAttribute("download")) return false;
    var url;
    try { url = new URL(link.href, location.href); } catch (e) { return false; }
    if (url.origin !== location.origin) return false;
    if (/\.csv$/.test(url.pathname) || /\/file$/.test(url.pathname)) return false;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return false;
    return true;
  }
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[href]");
    if (link && isPlainNavigation(link, event)) startProgress();
  });

  /* Prefetch navigation links only (sidebar, tab bar, more sheet). Never POST targets, exports or sign-out. */
  var prefetched = Object.create(null);
  function prefetch(link) {
    var url;
    try { url = new URL(link.href, location.href); } catch (e) { return; }
    if (url.origin !== location.origin) return;
    var path = url.pathname;
    if (path === "/logout" || path.indexOf("/api/") === 0 || /\.csv$/.test(path) || /\/file$/.test(path)) return;
    if (path === location.pathname && url.search === location.search) return;
    var key = path + url.search;
    if (prefetched[key]) return;
    prefetched[key] = true;
    var hint = document.createElement("link");
    hint.rel = "prefetch";
    hint.href = key;
    hint.as = "document";
    document.head.appendChild(hint);
  }
  function onIntent(event) {
    var link = event.target.closest && event.target.closest("a[data-prefetch]");
    if (link) prefetch(link);
  }
  document.addEventListener("pointerover", onIntent, { passive: true });
  document.addEventListener("focusin", onIntent);
  document.addEventListener("touchstart", onIntent, { passive: true });

  /* Forms: confirmation, busy state, progress. */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    var button = form.querySelector("button[type=submit], button:not([type])");
    if (button) {
      var busy = button.getAttribute("data-busy");
      if (busy) {
        var label = button.querySelector("[data-label]");
        if (label) label.textContent = busy;
      }
      button.classList.add("is-loading");
      button.setAttribute("aria-busy", "true");
      window.setTimeout(function () { button.disabled = true; }, 0);
    }
    if (form.hasAttribute("data-signin")) {
      document.body.classList.add("is-signing-in");
    }
    startProgress();
  });

  /* Auto-submit filter selects. */
  document.addEventListener("change", function (event) {
    var el = event.target;
    if (el && el.matches && el.matches("select[data-autosubmit]") && el.form) {
      startProgress();
      el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
    }
  });

  /* Close the mobile "More" sheet when tapping outside it. */
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details.more[open]").forEach(function (sheet) {
      if (!sheet.contains(event.target)) sheet.removeAttribute("open");
    });
  });

  /* Pages that ask to refresh themselves while work is running (e.g. a sync). */
  var refresher = document.querySelector("[data-refresh-seconds]");
  if (refresher) {
    var seconds = parseInt(refresher.getAttribute("data-refresh-seconds"), 10);
    if (seconds >= 3 && seconds <= 120) {
      window.setTimeout(function () {
        if (document.visibilityState === "visible") location.reload();
        else document.addEventListener("visibilitychange", function once() {
          if (document.visibilityState === "visible") { document.removeEventListener("visibilitychange", once); location.reload(); }
        });
      }, seconds * 1000);
    }
  }
})();
