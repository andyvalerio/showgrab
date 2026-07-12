// Converts every <time datetime="..."> element (rendered server-side in UTC,
// as a safe no-JS fallback) to the browser's own local timezone. No server
// setting, no configuration — the browser already knows what timezone it's in.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("time[datetime]").forEach((el) => {
    const d = new Date(el.getAttribute("datetime"));
    if (!isNaN(d)) {
      el.textContent = d.toLocaleString();
    }
  });
});
