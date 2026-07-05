/*
 * Light/dark theme toggle. Loaded synchronously in <head> so the saved theme
 * is applied before first paint (no flash). Falls back to prefers-color-scheme
 * when the user has not chosen a theme.
 */
(function () {
  var root = document.documentElement;

  try {
    var saved = localStorage.getItem("theme");
    if (saved === "dark" || saved === "light") root.dataset.theme = saved;
  } catch (e) {}

  function isDark() {
    return root.dataset.theme
      ? root.dataset.theme === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function icon() {
    return isDark() ? "☀" : "☾"; // ☀ when dark (click → light), ☾ when light
  }

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.querySelector(".theme-toggle");
    if (!button) return;
    button.textContent = icon();
    button.addEventListener("click", function () {
      var next = isDark() ? "light" : "dark";
      root.dataset.theme = next;
      try {
        localStorage.setItem("theme", next);
      } catch (e) {}
      button.textContent = icon();
    });
  });
})();
