/* Apply theme before paint. Keep this file sync (no defer) in <head>.
   Default is light. Dark is an explicit Settings choice, stored as light|dark. */
(function () {
  "use strict";
  var KEY = "vggos.theme";
  var root = document.documentElement;

  function read() {
    try { return window.localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function write(value) {
    try { window.localStorage.setItem(KEY, value); } catch (e) {}
  }
  function normalize(pref) {
    return pref === "dark" ? "dark" : "light";
  }
  function syncControls(theme) {
    document.querySelectorAll('input[name="theme"][data-theme-choice]').forEach(function (input) {
      input.checked = input.value === theme;
    });
    document.querySelectorAll("[data-theme-option]").forEach(function (card) {
      card.classList.toggle("is-selected", card.getAttribute("data-theme-option") === theme);
    });
  }
  function apply(pref) {
    var theme = normalize(pref != null ? pref : read());
    root.setAttribute("data-theme", theme);
    root.style.colorScheme = theme;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#0a0a0a" : "#edf9fd");
    syncControls(theme);
  }

  apply(read());
  window.vggosTheme = {
    apply: apply,
    current: function () {
      return root.getAttribute("data-theme") === "dark" ? "dark" : "light";
    },
    set: function (pref) {
      var theme = normalize(pref);
      write(theme);
      apply(theme);
    }
  };
})();
