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
      btn.setAttribute("title", on ? "Expand" : "Collapse");
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
    document.body.classList.add("is-navigating");
    if (!bar) return;
    bar.classList.remove("is-done");
    void bar.offsetWidth;
    bar.classList.add("is-active");
  }
  function stopProgress() {
    document.body.classList.remove("is-navigating");
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
    if (link && isPlainNavigation(link, event) && !link.hasAttribute("data-entity-switch")) startProgress();
  });

  /* Prefetch navigation links only (sidebar, tab bar, more sheet). Never POST targets, exports or sign-out. */
  var prefetched = Object.create(null);
  var fetchCache = Object.create(null);
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
    if (link.hasAttribute("data-entity-switch") && window.fetch) {
      fetch(key, { credentials: "same-origin", headers: { "X-VGGOS-Partial": "1" } })
        .then(function (response) { return response.ok ? response.text() : Promise.reject(); })
        .then(function (html) { fetchCache[key] = html; })
        .catch(function () {});
      return;
    }
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

  function setEntityCookie(code) {
    var secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie = "vggos_entity=" + encodeURIComponent(code) + "; Path=/; Max-Age=2592000; SameSite=Lax" + secure;
  }

  function applyEntityDocument(html, url, push) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var nextMain = doc.querySelector("#main");
    var nextBar = doc.querySelector("#contextbar");
    var main = document.querySelector("#main");
    var barNode = document.querySelector("#contextbar");
    if (!nextMain || !main) {
      location.assign(url);
      return;
    }
    main.replaceWith(nextMain);
    if (nextBar && barNode) barNode.replaceWith(nextBar);
    else if (nextBar && !barNode) {
      var shellMain = document.querySelector(".main");
      if (shellMain) shellMain.insertBefore(nextBar, shellMain.querySelector("#main"));
    }
    document.title = doc.title || document.title;
    if (push) history.pushState({ entitySoft: true }, "", url);
    stopProgress();
    document.body.classList.remove("is-entity-switching");
  }

  var entitySwitching = false;
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[data-entity-switch]");
    if (!link || !isPlainNavigation(link, event)) return;
    if (link.classList.contains("is-active")) {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    if (entitySwitching) return;
    entitySwitching = true;
    var url;
    try { url = new URL(link.href, location.href); } catch (e) { entitySwitching = false; return; }
    var code = link.getAttribute("data-entity") || url.searchParams.get("entity") || "";
    if (code) setEntityCookie(code);
    document.body.classList.add("is-entity-switching");
    startProgress();
    var key = url.pathname + url.search;
    var cached = fetchCache[key];
    var request = cached
      ? Promise.resolve(cached)
      : fetch(key, { credentials: "same-origin", headers: { "X-VGGOS-Partial": "1" } })
          .then(function (response) {
            if (!response.ok) throw new Error("switch failed");
            return response.text();
          });
    request
      .then(function (html) {
        fetchCache[key] = html;
        applyEntityDocument(html, key, true);
        entitySwitching = false;
      })
      .catch(function () {
        entitySwitching = false;
        location.assign(key);
      });
  });

  window.addEventListener("popstate", function () {
    if (!window.fetch) return;
    var key = location.pathname + location.search;
    startProgress();
    document.body.classList.add("is-entity-switching");
    fetch(key, { credentials: "same-origin", headers: { "X-VGGOS-Partial": "1" } })
      .then(function (response) {
        if (!response.ok) throw new Error("nav failed");
        return response.text();
      })
      .then(function (html) {
        applyEntityDocument(html, key, false);
      })
      .catch(function () {
        location.reload();
      });
  });

  /* Warm the other company tabs as soon as the shell is ready. */
  document.querySelectorAll("a[data-entity-switch]:not(.is-active)").forEach(function (link) {
    prefetch(link);
  });

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
