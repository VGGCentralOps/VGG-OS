/* Apply theme before paint. Keep this file sync (no defer) in <head>. */
(function () {
  "use strict";
  var KEY = "vggos.theme";
  var root = document.documentElement;
  function systemDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function read() {
    try { return window.localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function write(value) {
    try { window.localStorage.setItem(KEY, value); } catch (e) {}
  }
  function resolve(pref) {
    if (pref === "light" || pref === "dark") return pref;
    return systemDark() ? "dark" : "light";
  }
  function apply(pref) {
    var theme = resolve(pref || read() || "system");
    root.setAttribute("data-theme", theme);
    root.style.colorScheme = theme;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#0b1620" : "#edf9fd");
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      var next = theme === "dark" ? "light" : "dark";
      btn.setAttribute("aria-label", next === "dark" ? "Switch to dark mode" : "Switch to light mode");
      btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
      btn.dataset.themeNext = next;
    });
  }
  apply(read() || "system");
  window.vggosTheme = {
    apply: apply,
    toggle: function () {
      var current = root.getAttribute("data-theme") === "dark" ? "dark" : "light";
      var next = current === "dark" ? "light" : "dark";
      write(next);
      apply(next);
    }
  };
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
      if (!read() || read() === "system") apply("system");
    });
  }
})();
