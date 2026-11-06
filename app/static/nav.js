(function () {
  const cache = new Map();
  const page = document.querySelector(".portal-page");
  if (!page) return;

  function keyOf(url) {
    return url.pathname + url.search;
  }

  function isPageLink(url) {
    if (url.origin !== location.origin) return false;
    const path = url.pathname;
    if (path.startsWith("/static") || path.startsWith("/api") || path === "/login") return false;
    if (path.endsWith("/file") || path.endsWith(".csv")) return false;
    return true;
  }

  function setActive(path) {
    document.querySelectorAll(".side-nav a, .tabbar a").forEach(function (link) {
      const target = new URL(link.href, location.origin);
      const on = target.pathname === "/"
        ? path === "/"
        : path === target.pathname || (target.pathname === "/repositories" && path.startsWith("/repositories"));
      link.classList.toggle("is-active", on);
      if (on) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }

  function remember(url, html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const next = doc.querySelector(".portal-page");
    if (!next) return null;
    const item = { title: doc.title, html: next.innerHTML };
    cache.set(keyOf(url), item);
    if (cache.size > 40) cache.delete(cache.keys().next().value);
    return item;
  }

  function show(url, item, push) {
    page.innerHTML = item.html;
    page.classList.remove("is-waiting");
    document.title = item.title;
    setActive(url.pathname);
    const scroller = document.querySelector(".portal-scroll");
    if (scroller) scroller.scrollTop = 0;
    if (push) history.pushState({}, "", url.pathname + url.search);
    prefetchAround();
  }

  function load(url) {
    const key = keyOf(url);
    if (cache.has(key)) return Promise.resolve(cache.get(key));
    return fetch(url.pathname + url.search, { credentials: "same-origin", headers: { Accept: "text/html" } })
      .then(function (response) {
        if (response.redirected && response.url.indexOf("/login") !== -1) {
          location.assign("/login");
          return null;
        }
        if (!response.ok) {
          location.assign(url.pathname + url.search);
          return null;
        }
        return response.text().then(function (html) {
          return remember(new URL(response.url), html);
        });
      })
      .catch(function () {
        location.assign(url.pathname + url.search);
        return null;
      });
  }

  function open(url, push) {
    setActive(url.pathname);
    const hit = cache.get(keyOf(url));
    if (hit) {
      show(url, hit, push);
      return;
    }
    page.classList.add("is-waiting");
    load(url).then(function (item) {
      if (item) show(url, item, push);
    });
  }

  function prefetch(href) {
    try {
      const url = new URL(href, location.origin);
      if (!isPageLink(url) || cache.has(keyOf(url))) return;
      load(url);
    } catch (error) {
      return;
    }
  }

  function prefetchAround() {
    document.querySelectorAll(".side-nav a[href], .tabbar a[href], .portal-page a[href]").forEach(function (link) {
      prefetch(link.getAttribute("href"));
    });
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest("a[href]");
    if (!link) return;
    const url = new URL(link.href, location.origin);
    if (!isPageLink(url)) return;
    event.preventDefault();
    open(url, true);
  });

  document.addEventListener("pointerover", function (event) {
    const link = event.target.closest("a[href]");
    if (link) prefetch(link.getAttribute("href"));
  });

  window.addEventListener("popstate", function () {
    open(new URL(location.href), false);
  });

  if ("requestIdleCallback" in window) requestIdleCallback(prefetchAround);
  else setTimeout(prefetchAround, 50);
})();
