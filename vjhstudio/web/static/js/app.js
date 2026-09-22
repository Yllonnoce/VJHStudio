document.addEventListener('htmx:responseError', (e) => {
  const box = document.getElementById('toasts');
  if (!box) return;
  const t = document.createElement('div'); t.className = 'toast error';
  let msg = 'Request failed (' + e.detail.xhr.status + ')';
  try { msg = JSON.parse(e.detail.xhr.responseText).error || msg; } catch (_) {}
  t.textContent = msg; box.appendChild(t); setTimeout(() => t.remove(), 12000);
});

const VJH_DRAFT_KEY = 'vjh.generate.draft';
const VJH_FIELD_KEYS = [
  'subject', 'style', 'mood', 'lighting', 'camera', 'composition', 'colour', 'extras', 'negative',
];
// Reference roles, per mode. `reference` may be picked many times; every other role is a
// single slot, so its "+ Add" button disappears once it is filled.
const VJH_MODE_ROLES = { image: ['seed', 'reference'], video: ['first', 'last', 'reference'] };
const VJH_ROLE_LABELS = {
  seed: 'seed image', reference: 'reference', first: 'first frame', last: 'last frame',
};
const VJH_MULTI_ROLES = ['reference'];

// Chrome/Safari size a text area to its content on their own; anywhere else vjhAutosize
// (compose.js) does it by hand. That fallback writes an inline `height`, which would then
// out-rank `field-sizing: content` for the rest of the page's life -- so where the browser
// supports it, we must never touch `style.height` at all.
const VJH_FIELD_SIZING = (
  typeof CSS !== 'undefined' && !!CSS.supports && CSS.supports('field-sizing', 'content')
);

function vjhEmptyFields() {
  const f = {};
  VJH_FIELD_KEYS.forEach((k) => { f[k] = ''; });
  return f;
}

// Alpine component for the Generate page's prompt builder. The client-side preview
// mirrors services/prompts.py exactly via static/js/compose.js (composePrompt / buildNegative),
// so the composed prompt shown here is byte-for-byte what the server will build on submit.
window.generateForm = function (initial) {
  if (!initial) {
    try { initial = JSON.parse(document.getElementById('generate-initial').textContent); }
    catch (e) { initial = {}; }
  }
  initial = initial || {};

  return {
    fields: vjhEmptyFields(),
    finalPrompt: initial.final_prompt || '',
    noText: initial.mode !== 'video',  // mirrors VideoRequest's flipped default
    useDefaultNegative: true,
    defaultNegative: '',
    noTextTokens: '',
    mode: initial.mode === 'video' ? 'video' : 'image',
    refs: Array.isArray(initial.refs) ? initial.refs.slice() : [],
    pickerRole: 'reference',
    // the prompt library: the row this form was loaded from (posted back so the job
    // links it instead of creating a second one) and the polish blob to store on it
    promptId: initial.prompt_id || '',
    polishJson: '',
    polishMode: 'promptEnhance',

    // The shipped idea phrases, keyed by builder field (services/ideas.py). The chip
    // buttons themselves are rendered server-side so they work without JS; this copy is
    // what a future client-side row would bind to, and what keeps the two in step.
    ideas: (function () {
      try { return JSON.parse(document.getElementById('prompt-ideas').textContent); }
      catch (e) { return {}; }
    })(),

    get composed() {
      return window.composePrompt ? window.composePrompt(this.fields) : '';
    },

    get negativePreview() {
      if (!window.buildNegative) return '';
      return window.buildNegative(
        this.fields.negative, this.defaultNegative, this.useDefaultNegative,
        this.noText, this.noTextTokens,
      );
    },

    // ── idea chips ──────────────────────────────────────────────────────────
    // Both are thin wrappers over the pure helpers in compose.js (tested under node);
    // a chip only ever rewrites the field's own text, so the posted value and the
    // composed preview follow along exactly as if the phrase had been typed.
    toggleIdea(field, phrase) {
      if (!(field in this.fields) || !window.vjhToggleIdea) return;
      this.fields[field] = window.vjhToggleIdea(this.fields[field], phrase);
      this._grow(field);   // Extras is a text area: a chip can push it onto a new line
    },

    hasIdea(field, phrase) {
      if (!window.vjhHasIdea) return false;
      return window.vjhHasIdea(this.fields[field], phrase);
    },

    // Clipboard writes reject on an insecure origin or without permission; the preview
    // text is on screen either way, so a failure is silent by design.
    copyComposed() {
      try { navigator.clipboard.writeText(this.composed); } catch (e) { /* no clipboard */ }
    },

    // ── mode ────────────────────────────────────────────────────────────────
    // The tab button carries its own hx-get for #model-params; this only moves the
    // client-side state, and drops any picked reference the new mode cannot use.
    setMode(mode) {
      if (mode !== 'image' && mode !== 'video') return;
      this.mode = mode;
      if (mode === 'video') this.noText = false;  // the hidden box would still post `on`
      const allowed = VJH_MODE_ROLES[mode];
      this.refs = this.refs.filter((r) => allowed.indexOf(r.role) >= 0);
    },

    // ── reference chips ─────────────────────────────────────────────────────
    roleLabel(role) { return VJH_ROLE_LABELS[role] || role; },

    openRoles() {
      return VJH_MODE_ROLES[this.mode].filter((role) => (
        VJH_MULTI_ROLES.indexOf(role) >= 0 || !this.refs.some((r) => r.role === role)
      ));
    },

    addRef(detail) {
      if (!detail || !detail.id) return;
      const role = detail.role || 'reference';
      if (VJH_MODE_ROLES[this.mode].indexOf(role) < 0) return;
      const ref = {
        id: String(detail.id), role, name: detail.name || String(detail.id), thumb: detail.thumb || '',
      };
      if (VJH_MULTI_ROLES.indexOf(role) < 0) {
        this.refs = this.refs.filter((r) => r.role !== role);  // single slot: replace
      } else if (this.refs.some((r) => r.role === role && r.id === ref.id)) {
        return;  // the same asset twice in one role would just cost twice as much
      }
      this.refs.push(ref);
    },

    removeRef(index) { this.refs.splice(index, 1); },

    openPicker(role) {
      this.pickerRole = role;
      const dlg = document.getElementById('ref-picker');
      if (!dlg) return;
      if (window.htmx) {
        window.htmx.ajax('GET', '/hx/assets/picker?kind=image&role=' + encodeURIComponent(role),
          { target: '#ref-picker-body', swap: 'innerHTML' });
      }
      if (typeof dlg.showModal === 'function' && !dlg.open) dlg.showModal();
    },

    closePicker() {
      const dlg = document.getElementById('ref-picker');
      if (dlg && dlg.open) dlg.close();
    },

    // The only submit path: <form> has no hx-post of its own, because htmx binds a
    // verb and a URL once at process time and the mode can change afterwards. Each
    // mode owns a button with its own hx-post, and Enter clicks the active one.
    submitActive() {
      const btn = this.$refs[this.mode === 'video' ? 'submitVideo' : 'submitImage'];
      if (btn && !btn.disabled) btn.click();
    },

    // ── polish cards ─────────────────────────────────────────────────────────
    // Wired from the delegated click listener below (the panel is htmx-swapped, so no
    // handler can live on the button itself); `detail` is whatever vjhChoosePolish
    // (compose.js) built from the clicked card's data-* and the #polish-json blob.
    usePolish(detail) {
      detail = detail || {};
      this.finalPrompt = detail.text || '';
      this.polishJson = detail.polishJson || '';
      this._grow('final_prompt');   // a polished prompt is usually longer than the box
    },

    // ── save-prompt dialog ──────────────────────────────────────────────────
    openSaveDialog() {
      const d = document.getElementById('save-prompt');
      if (d && d.showModal) d.showModal();
    },

    closeSaveDialog() {
      const d = document.getElementById('save-prompt');
      if (d && d.close) d.close();
    },

    init() {
      this.defaultNegative = this.$el.dataset.defaultNegative || '';
      this.noTextTokens = this.$el.dataset.noTextTokens || '';
      this.polishMode = this.$el.dataset.polishMode || 'promptEnhance';
      // Restored drafts and remixes arrive after the browser sized the boxes, so grow
      // every text area once on load; @input keeps them in step from then on.
      if (!VJH_FIELD_SIZING) {
        this.$nextTick(() => {
          if (!window.vjhAutosize) return;
          this.$el.querySelectorAll('textarea').forEach((t) => window.vjhAutosize(t));
        });
      }
      window.addEventListener('ref-picked', (e) => this.addRef(e.detail || {}));
      window.addEventListener('use-polish', (e) => this.usePolish(e.detail || {}));

      if (initial.form) {
        Object.assign(this.fields, initial.form);
        this.finalPrompt = initial.final_prompt || '';
        if (typeof initial.form.no_text === 'boolean') this.noText = initial.form.no_text;
        if (typeof initial.form.use_default_negative === 'boolean') {
          this.useDefaultNegative = initial.form.use_default_negative;
        }
      }
      // A loaded prompt, remix or ref pick already carries the state a draft would
      // otherwise restore -- pulling the draft in on top would silently discard
      // whichever of the two just won, so it is skipped whenever any of the three is
      // present. Opening a prompt from the library also clears the stale draft
      // outright, so a later blank visit to /generate doesn't resurrect it.
      if (!(initial.form || initial.prompt_id || initial.remix)) {
        this._loadDraft();
      } else if (initial.prompt_id) {
        this._clearDraft();
      }

      const saveDraft = this._debounce(() => this._saveDraft(), 300);
      this.$watch('fields', saveDraft);
      this.$watch('finalPrompt', saveDraft);
      this.$watch('noText', saveDraft);
      this.$watch('useDefaultNegative', saveDraft);

      // A manual edit to any builder field means the composed text has drifted from
      // whatever `promptId` names -- drop the link so a submit lands on (or dedupes
      // by hash onto) whichever row the *new* text hashes to, instead of silently
      // overwriting the old row's final_prompt/polish_json with unrelated content.
      this.$watch('fields', () => { this.promptId = ''; });

      // The dialog posts to POST /prompts and gets an HX-Trigger back naming the row
      // it saved (or found); vjhUnwrapTrigger normalises the object payload the same
      // way it does job-finished's array one (see compose.js).
      document.body.addEventListener('prompt-saved', (e) => {
        const items = window.vjhUnwrapTrigger ? window.vjhUnwrapTrigger(e.detail) : [];
        const payload = items[0] || {};
        if (payload.id) this.promptId = String(payload.id);
        vjhToast(payload.created ? 'Prompt saved' : 'Already in your library', 'ok');
      });

      // A successful submit means the job now carries this prompt; clear the manual
      // override so the next preview reflects the (still-populated) builder fields.
      const form = document.getElementById('generate-form');
      if (form) {
        form.addEventListener('htmx:afterRequest', (e) => {
          const d = e.detail;
          if (!d || !d.successful || !d.xhr || d.xhr.status !== 200) return;
          // every request from inside the form bubbles here — the estimate, the model
          // options, the picker — but only a submit means the job took this prompt
          const path = (d.pathInfo && d.pathInfo.requestPath) || '';
          if (path.startsWith('/generate/')) this.finalPrompt = '';
        });
      }
    },

    // Grow one text area after a *programmatic* change (a chip, a polish card) -- typing
    // is covered by @input on the element itself. A no-op where the browser sizes text
    // areas itself, and after $nextTick so Alpine has written the new value first.
    _grow(name) {
      if (VJH_FIELD_SIZING || !window.vjhAutosize) return;
      this.$nextTick(() => {
        const el = this.$el.querySelector('textarea[name="' + name + '"]');
        if (el) window.vjhAutosize(el);
      });
    },

    _loadDraft() {
      try {
        const raw = localStorage.getItem(VJH_DRAFT_KEY);
        if (!raw) return;
        const draft = JSON.parse(raw);
        if (draft && draft.fields) Object.assign(this.fields, draft.fields);
        if (draft && typeof draft.finalPrompt === 'string') this.finalPrompt = draft.finalPrompt;
        // video mode must not send no_text (schemas/video.py flips the default), so a
        // draft saved from the Image tab never restores that box here
        if (draft && typeof draft.noText === 'boolean' && initial.mode !== 'video') {
          this.noText = draft.noText;
        }
        if (draft && typeof draft.useDefaultNegative === 'boolean') {
          this.useDefaultNegative = draft.useDefaultNegative;
        }
      } catch (e) { /* private-browsing / blocked storage: draft is best-effort only */ }
    },

    _saveDraft() {
      try {
        localStorage.setItem(VJH_DRAFT_KEY, JSON.stringify({
          fields: this.fields, finalPrompt: this.finalPrompt,
          noText: this.noText, useDefaultNegative: this.useDefaultNegative,
        }));
      } catch (e) { /* same as above */ }
    },

    _clearDraft() {
      try { localStorage.removeItem(VJH_DRAFT_KEY); } catch (e) { /* same as above */ }
    },

    _debounce(fn, ms) {
      let t;
      return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
    },
  };
};

// ── Toasts + desktop notifications on job completion ────────────────────────────
function vjhToast(text, kind, thumb) {
  const box = document.getElementById('toasts');
  if (!box) return;
  const t = document.createElement('div');
  t.className = 'toast ' + (kind || 'info');
  if (thumb) {
    const img = document.createElement('img');
    img.src = thumb; img.alt = ''; img.width = 40; img.height = 40;
    img.style.cssText = 'width:40px;height:40px;object-fit:cover;border-radius:4px;vertical-align:middle;margin-right:.5rem';
    t.appendChild(img);
  }
  const span = document.createElement('span');
  span.textContent = text;
  t.appendChild(span);
  box.appendChild(t);
  setTimeout(() => t.remove(), 8000);
}

// The HX-Trigger payload is a list of {id, status, title, thumb} — one entry per job
// that finished since the last poll (see routes/jobs.py::panel_ctx / "seen_at" stamping).
// htmx 2.x wraps a JSON-array trigger value as `e.detail = {value: [...], elt: ...}` rather
// than handing the array through as `e.detail` itself — vjhUnwrapTrigger (compose.js)
// normalises every shape back to a plain array.
document.body.addEventListener('job-finished', (e) => {
  const jobs = window.vjhUnwrapTrigger ? window.vjhUnwrapTrigger(e.detail) : [];
  jobs.forEach((d) => {
    if (!d || !d.id) return;
    const ok = d.status === 'succeeded';
    vjhToast(`${d.title}: ${d.status}`, ok ? 'ok' : 'error', d.thumb);
    if (window.Notification && Notification.permission === 'granted'
        && document.documentElement.dataset.notify === '1') {
      new Notification('VJHStudio', { body: `${d.title}: ${d.status}`, icon: d.thumb || '/static/img/favicon.svg' });
    }
  });
});

// Settings page "Test notification" button: request permission (a no-op if already
// granted/denied) then fire a sample. It never saves ui.notify_desktop itself — the
// Save button on the settings form is the only thing that persists that setting —
// but it does flip data-notify to '1' immediately when the checkbox is already
// checked, so a same-page job-finished right after clicking Save still fires.
function vjhTestNotification() {
  if (!window.Notification) return;
  const checkbox = document.querySelector('input[name="ui.notify_desktop"][type="checkbox"]');
  const fire = () => {
    if (Notification.permission !== 'granted') return;
    new Notification('VJHStudio', { body: 'Desktop notifications are working.', icon: '/static/img/favicon.svg' });
  };
  Notification.requestPermission().then((perm) => {
    if (perm === 'granted' && checkbox && checkbox.checked) {
      document.documentElement.dataset.notify = '1';
    }
    fire();
  });
}

// ── Lightbox (Gallery) ───────────────────────────────────────────────────────────
function vjhOpenLightbox() {
  const dlg = document.getElementById('lightbox');
  if (dlg && typeof dlg.showModal === 'function' && !dlg.open) dlg.showModal();
}

document.body.addEventListener('close-lightbox', () => {
  const dlg = document.getElementById('lightbox');
  if (dlg && dlg.open) dlg.close();
});

// ArrowLeft/ArrowRight step through neighbouring images when the detail partial
// provides `.lb-prev`/`.lb-next` controls; Esc closing the dialog is native <dialog>
// behaviour and needs no JS.
document.addEventListener('keydown', (e) => {
  const dlg = document.getElementById('lightbox');
  if (!dlg || !dlg.open) return;
  if (e.key === 'ArrowLeft') {
    const btn = dlg.querySelector('.lb-prev');
    if (btn) btn.click();
  } else if (e.key === 'ArrowRight') {
    const btn = dlg.querySelector('.lb-next');
    if (btn) btn.click();
  }
});

// ── Assets: dropzone drag/drop + upload progress ────────────────────────────────
(function () {
  const form = document.getElementById('asset-upload-form');
  if (!form) return;
  const input = document.getElementById('asset-files-input');

  ['dragenter', 'dragover'].forEach((evt) => {
    form.addEventListener(evt, (e) => {
      e.preventDefault();
      form.classList.add('is-dragover');
    });
  });
  ['dragleave', 'drop'].forEach((evt) => {
    form.addEventListener(evt, (e) => {
      e.preventDefault();
      form.classList.remove('is-dragover');
    });
  });
  // Dropping files assigns them straight to the hidden <input type=file> and submits
  // the form immediately; picking via the "choose files" label still requires the
  // explicit Upload click.
  form.addEventListener('drop', (e) => {
    const files = e.dataTransfer && e.dataTransfer.files;
    if (!files || !files.length || !input) return;
    input.files = files;
    if (typeof form.requestSubmit === 'function') form.requestSubmit();
    else form.submit();
  });
})();

// htmx dispatches htmx:xhr:progress on the requesting element itself (it bubbles),
// with detail = {loaded, total, lengthComputable}; the upload form owns a <progress>
// bar that fills during the request and hides again once it settles.
document.body.addEventListener('htmx:xhr:progress', (e) => {
  const d = e.detail || {};
  if (!d.elt || d.elt.id !== 'asset-upload-form') return;
  const bar = document.getElementById('asset-upload-progress');
  if (!bar || !d.lengthComputable) return;
  bar.hidden = false;
  bar.value = Math.round((d.loaded / d.total) * 100);
  if (d.loaded >= d.total) {
    setTimeout(() => {
      bar.hidden = true;
      bar.value = 0;
    }, 400);
  }
});

// ── Reference picker (Generate) ─────────────────────────────────────────────────
// The picker grid is swapped into <dialog id="ref-picker"> by htmx, so the click
// handler is delegated from the dialog rather than bound to the buttons themselves.
// Each button carries the four data-* attributes assets/_picker.html renders; the
// Alpine component listens for `ref-picked` on window and turns it into a chip.
document.addEventListener('click', (e) => {
  const dlg = document.getElementById('ref-picker');
  if (!dlg || !dlg.open || !dlg.contains(e.target)) return;
  const btn = e.target.closest('.asset-picker-item');
  if (!btn) return;
  e.preventDefault();
  window.dispatchEvent(new CustomEvent('ref-picked', {
    detail: {
      id: btn.dataset.assetId,
      thumb: btn.dataset.thumb || '',
      name: btn.dataset.name || '',
      role: btn.dataset.role || 'reference',
    },
  }));
  dlg.close();
});

// ── Polish cards (Generate) ─────────────────────────────────────────────────────
// #polish-results is swapped whole (outerHTML) on every "Polish with AI" click, so its
// "Use this" buttons need a delegated listener rather than an inline handler. The
// heavy lifting (parsing #polish-json, merging in the button's own data-*) is
// vjhChoosePolish (compose.js, a pure function so it gets a node test); this handler
// only reads the DOM, marks the chosen card and forwards the result to whichever
// generateForm instance is listening for `use-polish`.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.use-polish');
  if (!btn) return;
  const panel = document.getElementById('polish-results');
  if (!panel || !panel.contains(btn)) return;
  const script = document.getElementById('polish-json');
  const raw = script ? script.textContent : '';
  const index = parseInt(btn.dataset.index, 10) || 0;
  const detail = window.vjhChoosePolish
    ? window.vjhChoosePolish(raw, index, btn.dataset.text || '', btn.dataset.mode || '', btn.dataset.model || '')
    : { text: btn.dataset.text || '', polishJson: '' };
  panel.querySelectorAll('.polish-card.is-selected').forEach((c) => c.classList.remove('is-selected'));
  const card = btn.closest('.polish-card');
  if (card) card.classList.add('is-selected');
  window.dispatchEvent(new CustomEvent('use-polish', { detail }));
});

// Switching mode (or model) swaps #model-params for a partial whose fields the estimate
// depends on; the swap itself fires no `change`, so nudge the form once it lands.
document.body.addEventListener('htmx:afterSwap', (e) => {
  const target = e.detail && e.detail.target;
  if (!target || target.id !== 'model-params') return;
  const form = document.getElementById('generate-form');
  if (form && window.htmx) window.htmx.trigger(form, 'change');
});

// ── Restarting page ─────────────────────────────────────────────────────────────
// The app re-execs itself after an update, so /api/health first stops answering and
// then comes back with a new boot_id. Waiting for that new id (rather than for the
// first successful reply) is what stops us from redirecting into the dying process:
// waiting → down → up. After 2 minutes we stop guessing and show how to start it by
// hand. Exposed on window so the standalone page can call it with server values.
window.restartWatcher = function (bootId, returnTo) {
  const target = returnTo || '/';
  const deadline = Date.now() + 120000;
  const statusEl = document.getElementById('restart-status');
  let phase = 'waiting';

  const say = (text) => { if (statusEl) statusEl.textContent = text; };

  const giveUp = () => {
    const manual = document.getElementById('restart-manual');
    if (manual) manual.hidden = false;
    say('VJHStudio has not come back yet.');
  };

  const tick = async () => {
    if (Date.now() > deadline) { giveUp(); return; }
    try {
      const r = await fetch('/api/health', { cache: 'no-store' });
      const body = await r.json();
      if (body && body.boot_id && body.boot_id !== bootId) {
        phase = 'up';
        say('VJHStudio is back. Opening…');
        window.location.replace(target);
        return;
      }
      if (phase === 'down') say('VJHStudio is starting…');
    } catch (e) {
      if (phase === 'waiting') { phase = 'down'; say('VJHStudio has stopped. Waiting for it to start again…'); }
    }
    setTimeout(tick, 1000);
  };

  setTimeout(tick, 1000);
};
