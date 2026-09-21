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
    noText: true,
    useDefaultNegative: true,
    defaultNegative: '',
    noTextTokens: '',

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

    init() {
      this.defaultNegative = this.$el.dataset.defaultNegative || '';
      this.noTextTokens = this.$el.dataset.noTextTokens || '';

      if (initial.form) {
        Object.assign(this.fields, initial.form);
        this.finalPrompt = initial.final_prompt || '';
        if (typeof initial.form.no_text === 'boolean') this.noText = initial.form.no_text;
        if (typeof initial.form.use_default_negative === 'boolean') {
          this.useDefaultNegative = initial.form.use_default_negative;
        }
      } else {
        this._loadDraft();
      }

      const saveDraft = this._debounce(() => this._saveDraft(), 300);
      this.$watch('fields', saveDraft);
      this.$watch('finalPrompt', saveDraft);
      this.$watch('noText', saveDraft);
      this.$watch('useDefaultNegative', saveDraft);

      // A successful submit means the job now carries this prompt; clear the manual
      // override so the next preview reflects the (still-populated) builder fields.
      const form = document.getElementById('generate-form');
      if (form) {
        form.addEventListener('htmx:afterRequest', (e) => {
          const d = e.detail;
          if (d && d.successful && d.xhr && d.xhr.status === 200) this.finalPrompt = '';
        });
      }
    },

    _loadDraft() {
      try {
        const raw = localStorage.getItem(VJH_DRAFT_KEY);
        if (!raw) return;
        const draft = JSON.parse(raw);
        if (draft && draft.fields) Object.assign(this.fields, draft.fields);
        if (draft && typeof draft.finalPrompt === 'string') this.finalPrompt = draft.finalPrompt;
        if (draft && typeof draft.noText === 'boolean') this.noText = draft.noText;
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
