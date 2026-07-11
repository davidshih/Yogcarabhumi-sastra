/*
 * Light/dark theme toggle. Loaded synchronously in <head> so the saved theme
 * is applied before first paint (no flash). Light is the reading default.
 */
(function () {
  var root = document.documentElement;

  try {
    var saved = localStorage.getItem("theme");
    root.dataset.theme = saved === "dark" ? "dark" : "light";
  } catch (e) {}

  function isDark() {
    return root.dataset.theme === "dark";
  }

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.querySelector(".theme-toggle");
    if (!button) return;
    button.textContent = isDark() ? "淺色" : "深色";
    button.addEventListener("click", function () {
      var next = isDark() ? "light" : "dark";
      root.dataset.theme = next;
      try {
        localStorage.setItem("theme", next);
      } catch (e) {}
      button.textContent = isDark() ? "淺色" : "深色";
    });

    var rail = document.querySelector(".site-rail");
    var railToggle = document.querySelector(".rail-toggle");
    if (rail && railToggle) {
      railToggle.addEventListener("click", function () {
        var open = rail.classList.toggle("is-open");
        railToggle.setAttribute("aria-expanded", String(open));
      });
      document.addEventListener("click", function (event) {
        if (!rail.classList.contains("is-open")) return;
        if (rail.contains(event.target) || railToggle.contains(event.target)) return;
        rail.classList.remove("is-open");
        railToggle.setAttribute("aria-expanded", "false");
      });
    }
  });
})();
