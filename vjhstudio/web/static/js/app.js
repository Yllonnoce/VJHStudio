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
    // Set from #model-params' data-needs-first-frame on load and after every swap of
    // that panel (init() below): an image-to-video-only model has no text-only path,
    // so the References section says so before the pre-flight 422 has to.
    needsFirstFrame: false,
    // Same trick for #model-params' data-has-audio: a video model that advertises
    // "feat:audio" gets three sound chips at the end of the Extras row.
    hasAudio: false,
    uploading: '',
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
    // `single` marks a row whose phrases are mutually exclusive (Style: one medium per
    // picture), so the chip replaces the field instead of appending to it.
    toggleIdea(field, phrase, single) {
      if (!(field in this.fields) || !window.vjhToggleIdea) return;
      this.fields[field] = single && window.vjhSetIdea
        ? window.vjhSetIdea(this.fields[field], phrase)
        : window.vjhToggleIdea(this.fields[field], phrase);
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
    // "Reset" on the Generate page: back to an empty form in the current mode. Clears
    // every builder field (and so every chip), the final prompt, polish state, the
    // reference chips and the saved draft, then re-fetches the parameter panel so
    // sizes/steps return to the model's defaults. The model and project stay.
    resetForm() {
      this.fields = vjhEmptyFields();
      this.finalPrompt = '';
      this.polishJson = '';
      this.promptId = '';
      this.refs = [];
      this.noText = this.mode !== 'video';
      this.useDefaultNegative = true;
      this._clearDraft();
      const select = document.getElementById(this.mode === 'video' ? 'video-model-select' : 'model-select');
      const air = select ? select.value : '';
      if (window.htmx) {
        window.htmx.ajax('GET', '/hx/model-options?mode=' + this.mode + '&air=' + encodeURIComponent(air), { target: '#model-params', swap: 'outerHTML' });
      }
      const results = document.getElementById('polish-results');
      if (results) results.innerHTML = '';
      this.$nextTick(() => { this.$el.querySelectorAll('textarea').forEach((el) => this.autosize(el)); });
    },

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

    // "Upload first frame" (and the seed/last/reference twins): post the picked file
    // straight to the Assets library, then drop the stored asset into this role with
    // the same addRef() the picker dialog feeds. Asking for JSON is what makes
    // /assets/upload answer with {assets: [{id, name, thumb_url, kind}], errors: [...]}
    // instead of the grid; `uploading` dims every upload control until this one is done.
    async uploadRef(role, input) {
      const file = input && input.files && input.files[0];
      if (!file) return;
      input.value = '';   // so picking the same file again still fires `change`
      if (this.uploading) return;  // one at a time: the others are dimmed meanwhile
      this.uploading = role;
      try {
        const res = await fetch('/assets/upload', {
          method: 'POST',
          headers: { Accept: 'application/json' },
          body: (function () { const fd = new FormData(); fd.append('files', file); return fd; })(),
        });
        let body = null;
        try { body = await res.json(); } catch (e) { body = null; }
        const problems = (body && body.errors) || [];
        if (!res.ok) {
          vjhToast(problems[0] || ('Upload failed (' + res.status + ')'), 'error');
          return;
        }
        const asset = ((body && body.assets) || [])[0];
        if (!asset || !asset.id) {
          vjhToast(problems[0] || 'Upload failed: nothing was stored.', 'error');
          return;
        }
        // a 200 can still carry refusals (a multi-file post); none of them is fatal here
        problems.forEach((msg) => vjhToast(msg, 'error'));
        this.addRef({ id: asset.id, name: asset.name, thumb: asset.thumb_url || '', role });
      } catch (e) {
        vjhToast('Upload failed: ' + e, 'error');
      } finally {
        this.uploading = '';
      }
    },

    // #model-params is htmx-swapped whole on every model/mode change, so the flag is
    // re-read from the fresh markup rather than pushed in; reading the live node keeps
    // this right whichever element htmx fired afterSwap on.
    _syncNeedsFirstFrame() {
      const panel = document.getElementById('model-params');
      this.needsFirstFrame = !!panel && panel.dataset.needsFirstFrame === 'true';
    },

    _syncHasAudio() {
      const panel = document.getElementById('model-params');
      this.hasAudio = !!panel && panel.dataset.hasAudio === '1';
    },

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
      this._syncNeedsFirstFrame();
      document.addEventListener('htmx:afterSwap', () => this._syncNeedsFirstFrame());
      this._syncHasAudio();
      document.addEventListener('htmx:afterSwap', () => this._syncHasAudio());
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

    // Grow the text area the user is typing in (@input on the element itself). Gated
    // exactly like _grow: where the browser supports `field-sizing: content` it already
    // sizes the box, and writing an inline `height` here would out-rank that rule for
    // the rest of the page's life -- freezing the box at whatever height the first
    // keystroke happened to produce.
    autosize(el) {
      if (VJH_FIELD_SIZING || !window.vjhAutosize) return;
      window.vjhAutosize(el);
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
// `show()`, not `showModal()`: a modal dialog goes into the browser's top layer,
// which paints over the sticky top bar and makes it unclickable. Non-modal keeps
// the bar usable while the details are open; the dialog is sized to sit below it
// (app.css). What a modal would have given for free -- Esc, and a click outside
// the panel -- is re-added below.
function vjhOpenLightbox() {
  const dlg = document.getElementById('lightbox');
  if (!dlg || typeof dlg.show !== 'function' || dlg.open) return;
  dlg.show();
  // A modal dialog stops the page behind it from scrolling; this one has to say so.
  document.documentElement.classList.add('vjh-lightbox-open');
}

// One place to undo that, on the dialog's own event: Esc, the close button, a click
// on the dim area and the `close-lightbox` that a delete fires all end up here.
//
// Delegated, and in the capture phase: boosted navigation swaps <main>, so the
// <dialog> the gallery renders is a *different node* on every visit -- a listener
// bound to the one that existed at load would stop firing, and the page would stay
// scroll-locked for ever. `close` does not bubble, so a listener on `document` only
// sees it going down the capture path, never coming back up.
document.addEventListener('close', (e) => {
  if (e.target && e.target.id === 'lightbox') {
    document.documentElement.classList.remove('vjh-lightbox-open');
  }
}, true);

document.body.addEventListener('close-lightbox', () => {
  const dlg = document.getElementById('lightbox');
  if (dlg && dlg.open) dlg.close();
});

// A click that lands on the dialog itself is a click on the dim area around the
// panel (the article), so it closes -- the same as clicking a modal's backdrop.
// Both ends of the click have to be the dim area, or selecting text in the panel
// and releasing the button outside it would close the details.
let vjhLightboxDownTarget = null;
document.addEventListener('mousedown', (e) => {
  vjhLightboxDownTarget = e.target;
});
document.addEventListener('click', (e) => {
  const dlg = document.getElementById('lightbox');
  if (dlg && dlg.open && e.target === dlg && vjhLightboxDownTarget === dlg) dlg.close();
});

// ArrowLeft/ArrowRight step through neighbouring images when the detail partial
// provides `.lb-prev`/`.lb-next` controls. Esc is free for a modal dialog only,
// so it is handled here.
document.addEventListener('keydown', (e) => {
  const dlg = document.getElementById('lightbox');
  if (!dlg || !dlg.open) return;
  if (e.key === 'Escape') {
    // Only swallow the key when it was aimed at the dialog: elsewhere on the page
    // Esc still belongs to whatever has focus (a select, a search field).
    if (dlg.contains(e.target)) e.preventDefault();
    dlg.close();
  } else if (e.key === 'ArrowLeft') {
    const btn = dlg.querySelector('.lb-prev');
    if (btn) btn.click();
  } else if (e.key === 'ArrowRight') {
    const btn = dlg.querySelector('.lb-next');
    if (btn) btn.click();
  }
});

// Deleting an output closes the dialog and re-fetches the grid, which throws away
// the card the focus was on -- browsers drop focus to <body> there, and a keyboard
// is then back at the top of the document. Put it on the first card instead.
document.body.addEventListener('htmx:afterSwap', (e) => {
  // The grid swaps itself with `outerHTML`, and htmx fires the event on the PARENT of
  // a swapped-away element, so the new grid is looked for under the target too.
  const t = e.target;
  if (!t) return;
  if (t.id !== 'gallery-grid' && !(t.querySelector && t.querySelector('#gallery-grid'))) return;
  // Focus is "lost" when it is on <body>, on a node the swap threw away, or -- what
  // actually happens after a delete -- still on the close button of the dialog that
  // has just been hidden. (checkVisibility() was measured returning true there, so
  // the closed dialog is asked directly.)
  const a = document.activeElement;
  const dlg = document.getElementById('lightbox');
  const lost =
    !a || a === document.body || !a.isConnected || (dlg && !dlg.open && dlg.contains(a));
  if (!lost) return;
  const card = document.querySelector('button.gallery-thumb, button.gallery-details');
  if (card) card.focus();
});

// ── Assets: dropzone drag/drop + upload progress ────────────────────────────────
// Delegated from `document` and resolved per event: boosted navigation swaps <main>,
// so the upload form does not exist yet on a cold load of any other page, and it is a
// new node every time you come back to /assets. Binding at load time would leave the
// dropzone dead on exactly the visits that matter.
(function () {
  function dropzone(e) {
    const form = document.getElementById('asset-upload-form');
    return form && form.contains(e.target) ? form : null;
  }

  ['dragenter', 'dragover'].forEach((evt) => {
    document.addEventListener(evt, (e) => {
      const form = dropzone(e);
      if (!form) return;   // outside the form the browser's "no drop" default stands
      e.preventDefault();
      form.classList.add('is-dragover');
    });
  });
  ['dragleave', 'drop'].forEach((evt) => {
    document.addEventListener(evt, (e) => {
      const form = dropzone(e);
      if (!form) return;
      e.preventDefault();
      form.classList.remove('is-dragover');
    });
  });
  // Dropping files assigns them straight to the hidden <input type=file> and submits
  // the form immediately; picking via the "choose files" label still requires the
  // explicit Upload click.
  document.addEventListener('drop', (e) => {
    const form = dropzone(e);
    if (!form) return;
    const input = document.getElementById('asset-files-input');
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

// ── Sticky top bar: publish its height so sticky rails and anchors sit below it ──
// Exported, because boosted navigation (below) has to re-measure: the nav wraps onto a
// second line as soon as the Update badge or a busy Queue chip appears, and the page
// that was just swapped in needs the new height for its sticky rails and anchors.
window.vjhMeasureHeader = function () {
  var h = document.querySelector('body > header');
  if (!h) return;
  document.documentElement.style.setProperty('--vjh-header-h', h.offsetHeight + 'px');
};

(function () {
  var measure = window.vjhMeasureHeader;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', measure);
  else measure();
  window.addEventListener('resize', measure);
  window.addEventListener('load', measure);
})();

// ── Boosted navigation: the top bar stays put, only <main> is swapped ────────────
// The header's links carry hx-boost (templates/partials/_boost.html), so a menu click
// fetches the page and htmx puts just its <main> in place. The header is therefore not
// re-rendered, and the two things the server used to settle for us have to be redone
// here: which link is the current one, and how tall the bar ended up.
//
// The "current" rule is _header.html's macro, to the letter: "/" matches only the exact
// path, every other link matches when the path sits under it (/generate/video is still
// Generate). Only links the macro itself would mark carry `data-navlink`, so the logo
// and the "Update available" / "No API key" chips (both /settings...) are left alone,
// exactly as they are server-side.
//
// The Queue chip is one of those `data-navlink`s, and this is the *only* thing that
// keeps it right: it re-renders itself every 10 seconds from /hx/jobs/badge, and the
// server cannot know which page that poll belongs to. (It was once told, via a `?at=`
// on the poll URL. htmx reads hx-get once, when it processes the element, and closes
// over that string for good -- so the parameter was frozen at the cold-load path and
// rewriting the attribute afterwards changed nothing.) Because the markup that comes
// back is server-rendered, the settle below has to run after *every* swap.
window.vjhMarkCurrentNav = function () {
  var path = window.location.pathname;
  var links = document.querySelectorAll('body > header nav a[data-navlink]');
  Array.prototype.forEach.call(links, function (a) {
    var href = a.getAttribute('href') || '';
    var current = href === '/' ? path === '/' : (href !== '' && path.indexOf(href) === 0);
    if (current) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
};

(function () {
  if (!window.htmx) return;   // restarting.html loads app.js on its own, without htmx

  // Back/forward: htmx snapshots the page body into localStorage as you leave a page
  // and replays it on popstate. The Generate page opts out (hx-history="false" on its
  // <main>), because replaying an Alpine-rendered snapshot would double its x-for
  // chips; with this flag the resulting cache miss becomes a plain reload of the
  // restored URL instead of a second fetch stitched into the old body.
  window.htmx.config.refreshOnHistoryMiss = true;

  function settle() {
    window.vjhMarkCurrentNav();
    window.vjhMeasureHeader();
  }

  // pushedIntoHistory/replacedInHistory fire after history.pushState but before the
  // swap, so location is already the new one; the extra frame lets the new <main>
  // land before the bar is measured.
  function settleSoon() {
    settle();
    setTimeout(settle, 0);
  }

  document.addEventListener('htmx:pushedIntoHistory', settleSoon);
  document.addEventListener('htmx:replacedInHistory', settleSoon);
  document.addEventListener('htmx:historyRestore', settleSoon);
  window.addEventListener('popstate', settleSoon);
  // EVERY swap, not just a navigation: the Queue chip's own 10-second poll replaces it
  // with markup the server rendered for /hx/jobs/badge, and the queue panel re-renders
  // it out of band as well. Both arrive unmarked, and this is what marks them. (It also
  // covers the nav rewrapping when the Update chip appears.)
  document.addEventListener('htmx:afterSettle', settle);

  // The cold-load markup is already right, but the JS has to agree with it from the
  // start -- otherwise the first poll would be the first time the chip was ever marked
  // by this code, on a page whose header it had never looked at.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', settle);
  else settle();
})();

// ── Model dropdown sort (Price / Name) ───────────────────────────────────────
// Reorders <option>s in place so the select (and the htmx/Alpine listeners bound to
// it) is never replaced. Options without data-name (the "(not in catalog)" sentinel or
// the empty promptEnhance choice) keep their place at the top. The choice persists.
var VJH_SORT_KEY = 'vjh.model-sort';
function vjhSortSelect(select, key) {
  if (!select || !window.vjhSortOptions) return;
  var opts = Array.prototype.slice.call(select.options);
  var pinned = opts.filter(function (o) { return !o.hasAttribute('data-name'); });
  var items = opts.filter(function (o) { return o.hasAttribute('data-name'); }).map(function (o) {
    return { value: o.value, name: o.dataset.name, price: o.dataset.price, el: o };
  });
  var current = select.value;
  var sorted = window.vjhSortOptions(items, key);
  pinned.forEach(function (o) { select.appendChild(o); });
  sorted.forEach(function (it) { select.appendChild(it.el); });
  select.value = current;
  document.querySelectorAll('[data-sort-select="' + select.id + '"]').forEach(function (b) {
    b.setAttribute('aria-pressed', b.dataset.sort === key ? 'true' : 'false');
  });
}
function vjhApplySavedSort(root) {
  var key = 'price';
  try { key = localStorage.getItem(VJH_SORT_KEY) || 'price'; } catch (e) { /* storage may be blocked */ }
  (root || document).querySelectorAll('select[data-sortable]').forEach(function (s) { vjhSortSelect(s, key); });
}
document.addEventListener('click', function (e) {
  var btn = e.target.closest('[data-sort-select]');
  if (!btn) return;
  var key = btn.dataset.sort === 'name' ? 'name' : 'price';
  try { localStorage.setItem(VJH_SORT_KEY, key); } catch (err) { /* same as above */ }
  vjhApplySavedSort(document);
});
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { vjhApplySavedSort(document); });
else vjhApplySavedSort(document);
document.addEventListener('htmx:afterSettle', function (e) { vjhApplySavedSort(e.target || document); });
document.addEventListener('htmx:historyRestore', function () { vjhApplySavedSort(document); });
