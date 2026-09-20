document.addEventListener('htmx:responseError', (e) => {
  const box = document.getElementById('toasts');
  if (!box) return;
  const t = document.createElement('div'); t.className = 'toast error';
  let msg = 'Request failed (' + e.detail.xhr.status + ')';
  try { msg = JSON.parse(e.detail.xhr.responseText).error || msg; } catch (_) {}
  t.textContent = msg; box.appendChild(t); setTimeout(() => t.remove(), 12000);
});

// Alpine component for the Generate page. Task 7 replaces the body of this with the
// real client-side mirror of services/prompts.py; until then the preview stays empty
// and every field is a plain form input, so the server sees exactly the same form.
window.generateForm = function (initial) {
  if (!initial) {
    try { initial = JSON.parse(document.getElementById('generate-initial').textContent); }
    catch (e) { initial = {}; }
  }
  return {
    f: Object.assign({ subject: '', style: '', mood: '', lighting: '', camera: '',
                       composition: '', colour: '', extras: '', negative: '',
                       no_text: true, use_default_negative: true }, initial.form || {}),
    finalPrompt: initial.final_prompt || '',
    composed: '',
    negativePreview: '',
  };
};

// Desktop notification when a job lands, when the browser has granted permission.
document.body.addEventListener('job-finished', (e) => {
  const jobs = Array.isArray(e.detail) ? e.detail : [e.detail];
  const box = document.getElementById('toasts');
  jobs.forEach((j) => {
    if (!j || !box) return;
    const t = document.createElement('div');
    t.className = 'toast ' + (j.status === 'succeeded' ? 'info' : 'error');
    t.textContent = (j.title || 'Job') + ' — ' + j.status;
    box.appendChild(t); setTimeout(() => t.remove(), 8000);
  });
});
