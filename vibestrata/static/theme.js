/* Applied before first paint: a saved light/dark choice; otherwise the system's. */
try {
  var th = localStorage.getItem("vibestrata.theme");
  if (th === "light" || th === "dark") document.documentElement.dataset.theme = th;
} catch (e) { /* storage blocked: follow the system */ }
