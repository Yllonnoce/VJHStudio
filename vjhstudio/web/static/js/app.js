function rsToggleTheme() {
  const el = document.documentElement;
  const next = el.dataset.theme === 'dark' ? 'light' : 'dark';
  el.dataset.theme = next;
  const fd = new FormData(); fd.append('ui.theme', next);
  fetch('/settings', { method: 'POST', body: fd });
}
document.addEventListener('htmx:responseError', (e) => {
  const box = document.getElementById('toasts');
  if (!box) return;
  const t = document.createElement('div'); t.className = 'toast error';
  let msg = 'Request failed (' + e.detail.xhr.status + ')';
  try { msg = JSON.parse(e.detail.xhr.responseText).error || msg; } catch (_) {}
  t.textContent = msg; box.appendChild(t); setTimeout(() => t.remove(), 12000);
});
