/* ── VJHStudio Theme Engine ───────────────────────────────────────────────────
   Loaded with `defer`, so nothing here runs before first paint.  The pre-paint
   work — applying the remembered theme so there is no flash of the wrong
   colours — is the small inline script in base.html.

   Source of truth is the `ui.theme` setting in the database, which the server
   renders as `data-server-theme` on <html>.  localStorage is only a fast path
   for that first paint, so on DOMContentLoaded the two are reconciled and the
   server value wins.
   ──────────────────────────────────────────────────────────────────────── */

/* Picker order: all light themes first, then all dark — the `dark` flag
   draws the group label, so keep each group contiguous. */
const VJH_THEMES = [
  { id: 'daylight',   name: 'Daylight',  swatch: '#2f6fed' },
  { id: 'classic',    name: 'Classic',   swatch: '#c9a84c' },
  { id: 'forest',     name: 'Forest',    swatch: '#6ecf80' },
  { id: 'ocean',      name: 'Ocean',     swatch: '#18c8e8' },
  { id: 'frost',      name: 'Frost',     swatch: '#90d8f8' },
  { id: 'parchment',  name: 'Parchment', swatch: '#e0b860' },
  { id: 'midnight',   name: 'Midnight',  swatch: '#00e5c8', dark: true },
  { id: 'crimson',    name: 'Crimson',   swatch: '#f05050', dark: true },
  { id: 'ember',      name: 'Ember',     swatch: '#ffa020', dark: true },
  { id: 'royal',      name: 'Royal',     swatch: '#b888ff', dark: true },
  { id: 'steel',      name: 'Steel',     swatch: '#9abcd4', dark: true },
];

var VJH_DARK_THEMES = VJH_THEMES.filter(function (t) { return t.dark; }).map(function (t) { return t.id; });

/* ── Contrast utility ────────────────────────────────────────────────────────
   Returns '#ffffff' or a dark near-black depending on which provides the
   higher WCAG contrast ratio against the given hex background.
   Used to ensure text is always readable on any coloured surface.           */
function vjhContrastText(hex) {
  hex = hex.replace(/^#/, '');
  if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
  var r = parseInt(hex.slice(0,2),16) / 255;
  var g = parseInt(hex.slice(2,4),16) / 255;
  var b = parseInt(hex.slice(4,6),16) / 255;
  // Gamma-correct perceived luminance (WCAG formula)
  function lin(c) { return c <= 0.04045 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); }
  var L = 0.2126*lin(r) + 0.7152*lin(g) + 0.0722*lin(b);
  // Contrast ratio against white vs near-black (#111118)
  var onDark  = (L + 0.05) / (0.004 + 0.05);   // contrast vs #111118 (L≈0.004)
  var onLight = (1.0 + 0.05) / (L + 0.05);      // contrast vs #ffffff
  return onLight > onDark ? '#ffffff' : '#111118';
}

/* Apply --sp-on-accent as an inline custom property so it overrides the
   theme stylesheet value and is always correct for the current accent.      */
function _vjhApplyOnAccent() {
  var accent = getComputedStyle(document.documentElement)
                 .getPropertyValue('--sp-accent').trim();
  if (!accent) return;
  var best = vjhContrastText(accent);
  document.documentElement.style.setProperty('--sp-on-accent', best);
  // The browser paints the phone's status bar / tab strip from <meta theme-color>;
  // base.html ships the Midnight accent, this follows the chosen theme.
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', accent);
}

/* ── Storage helpers ─────────────────────────────────────────────────────────
   localStorage throws in privacy modes that block site storage; an unguarded
   access there would abort this file and leave the picker unbuilt.          */
function _vjhReadStored() {
  try { return localStorage.getItem('vjh-theme'); } catch (e) { return null; }
}

function _vjhWriteStored(id) {
  try { localStorage.setItem('vjh-theme', id); } catch (e) { /* not persisted here */ }
}

function _vjhCurrentTheme() {
  return document.documentElement.getAttribute('data-theme') || 'midnight';
}

function _vjhApplyTheme(id) {
  document.documentElement.setAttribute('data-theme', id);
  document.documentElement.setAttribute('data-scheme', VJH_DARK_THEMES.indexOf(id) >= 0 ? 'dark' : 'light');
}

/* Reconcile the pre-paint fast path with the saved setting: whatever the
   server rendered from the database wins, and this browser's copy is
   corrected so the next first paint is already right.                       */
function _vjhReconcileTheme() {
  var server = document.documentElement.dataset.serverTheme;
  if (!server) return;
  if (_vjhReadStored() !== server) {
    _vjhApplyTheme(server);
    _vjhWriteStored(server);
  }
}

/* ── Public API ─────────────────────────────────────────────────────────── */
function vjhSetTheme(id) {
  _vjhApplyTheme(id);
  _vjhWriteStored(id);
  document.documentElement.dataset.serverTheme = id;
  // Recompute on-accent after CSS vars have updated (next microtask)
  requestAnimationFrame(_vjhApplyOnAccent);
  _vjhUpdateSwatchStates(id);
  // Durable copy: fire-and-forget POST, localStorage is the fast path.
  var fd = new FormData(); fd.append('ui.theme', id);
  fetch('/settings', { method: 'POST', body: fd });
}

function vjhTogglePicker() {
  var p = document.getElementById('vjh-theme-panel');
  if (!p) return;
  var open = p.style.display === 'block';
  p.style.display = open ? 'none' : 'block';
  if (!open) _vjhUpdateSwatchStates(_vjhCurrentTheme());
}

function _vjhUpdateSwatchStates(activeId) {
  document.querySelectorAll('[data-vjh-swatch]').forEach(function (el) {
    var on = el.dataset.vjhSwatch === activeId;
    el.style.outline       = on ? '3px solid var(--sp-accent)' : '2px solid transparent';
    el.style.outlineOffset = on ? '2px'              : '0';
    el.style.transform     = on ? 'scale(1.22)'      : 'scale(1)';
  });
}

/* ── Swatch clicks are delegated ─────────────────────────────────────────────
   htmx's history cache replays a *snapshot of the markup* on Back, which brings
   the swatch buttons back without the `onclick` properties they were built with
   -- markup carries attributes, never JS properties. The grid is rebuilt only if
   the restored snapshot has none; the click itself is read off the
   `data-vjh-swatch` attribute, which does survive.                            */
document.addEventListener('click', function (e) {
  var el = e.target && e.target.closest ? e.target.closest('[data-vjh-swatch]') : null;
  if (!el) return;
  vjhSetTheme(el.dataset.vjhSwatch);
});

/* ── Build swatch grid once DOM is ready ────────────────────────────────── */
function _vjhBuildSwatches() {
  var container = document.getElementById('vjh-swatches');
  if (!container || container.querySelector('[data-vjh-swatch]')) return;

  var active = _vjhCurrentTheme();

  function addGroupLabel(text) {
    var el = document.createElement('div');
    el.textContent = text;
    el.style.cssText = 'grid-column:1/-1;font-size:.62rem;font-weight:600;' +
                       'letter-spacing:.08em;text-transform:uppercase;' +
                       'color:var(--sp-muted);border-bottom:1px solid var(--sp-border);' +
                       'padding-bottom:3px;margin-bottom:-4px;';
    container.appendChild(el);
  }

  var darkStarted = false;
  addGroupLabel('Light');
  VJH_THEMES.forEach(function (t) {
    if (t.dark && !darkStarted) { darkStarted = true; addGroupLabel('Dark'); }

    var btn = document.createElement('button');
    btn.title             = t.name;
    btn.dataset.vjhSwatch = t.id;
    btn.style.cssText     = [
      'width:32px', 'height:32px', 'border-radius:50%', 'border:none',
      'cursor:pointer', 'padding:0', 'transition:transform .15s, outline .1s',
      'background:' + t.swatch,
      'display:block',
    ].join(';');

    var label = document.createElement('div');
    label.textContent   = t.name;
    label.style.cssText = 'font-size:.62rem;margin-top:3px;text-align:center;' +
                          'color:var(--sp-muted);line-height:1.2;';

    var wrap = document.createElement('div');
    wrap.style.cssText  = 'display:flex;flex-direction:column;align-items:center;';
    wrap.appendChild(btn);
    wrap.appendChild(label);
    container.appendChild(wrap);
  });

  _vjhUpdateSwatchStates(active);
}

document.addEventListener('DOMContentLoaded', function () {
  // The saved setting beats whatever this browser remembered
  _vjhReconcileTheme();
  // Compute and apply on-accent now that CSS has loaded
  _vjhApplyOnAccent();
  _vjhBuildSwatches();
});

// A history restore replaces the whole body, so the picker comes back as markup only.
document.addEventListener('htmx:historyRestore', function () {
  _vjhApplyOnAccent();
  _vjhBuildSwatches();
  _vjhUpdateSwatchStates(_vjhCurrentTheme());
});

/* ── Close picker when clicking outside ────────────────────────────────── */
document.addEventListener('click', function (e) {
  var p   = document.getElementById('vjh-theme-panel');
  var btn = document.getElementById('vjh-theme-btn');
  if (!p || p.style.display !== 'block') return;
  if (!p.contains(e.target) && btn && !btn.contains(e.target)) {
    p.style.display = 'none';
  }
});
