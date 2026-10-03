// ── Chat page ──────────────────────────────────────────────────────────────────────
// The page is server-rendered (pages/chat.html); this script sends a message and reads
// the reply as it is written. /chat/send answers with an event stream:
//   start {chat_id, url, title, new, user_html}   the message was saved
//   delta {text}                                   a piece of the reply
//   done  {html, total, retry}                     the finished reply, rendered
// It is read with fetch, not EventSource: an EventSource reconnects by itself after a
// dropped connection, and here that would silently re-send a billed request.
//
// Everything is delegated from document, because the top-bar links are boosted: this
// page's <main> can arrive by swap long after the script ran.
(() => {
  'use strict';
  const $ = (sel, root) => (root || document).querySelector(sel);
  const page = () => $('#chat-page');
  const form = () => $('#chat-form');
  const thread = () => $('#chat-thread');
  let controller = null; // the reply being read, if any

  // ── small helpers ──
  function showError(text) {
    const box = $('#chat-error');
    if (!box) return;
    box.textContent = text || '';
    box.hidden = !text;
  }

  function nearBottom() {
    return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
  }

  function toBottom() {
    window.scrollTo(0, document.documentElement.scrollHeight);
  }

  function setBusy(on) {
    const send = $('#chat-send'), stop = $('#chat-stop');
    if (send) send.hidden = on;
    if (stop) stop.hidden = !on;
  }

  function chatId() {
    const f = form();
    return f ? f.elements.chat_id.value : '';
  }

  // The model dropdown decides whether pictures can be shown at all.
  function syncVision() {
    const f = form();
    if (!f) return;
    const opt = f.elements.model.selectedOptions[0];
    const sees = !!opt && opt.dataset.vision === '1';
    const hasPictures = !!$('#chat-chips .chat-chip') || (page() && page().dataset.hasPictures === '1');
    $('#chat-attach').hidden = !sees;
    $('#chat-chips').hidden = !sees;
    $('#chat-blind').hidden = sees || !hasPictures;
    if (!sees) $('#chat-picker').hidden = true;
  }

  function refreshList() {
    const list = $('#chat-list');
    if (!list) return;
    fetch('/hx/chat/list?current=' + encodeURIComponent(chatId()))
      .then((r) => (r.ok ? r.text() : null))
      .then((html) => { if (html && $('#chat-list')) $('#chat-list').outerHTML = html; })
      .catch(() => {});
  }

  function reloadThread() {
    const id = chatId();
    if (!id) return;
    fetch('/hx/chat/' + encodeURIComponent(id) + '/thread')
      .then((r) => (r.ok ? r.text() : null))
      .then((html) => { if (html && thread()) { thread().innerHTML = html; toBottom(); } })
      .catch(() => {});
  }

  // ── pictures ──
  function addChip(pic) {
    const chips = $('#chat-chips');
    if (!chips || !pic || chips.querySelector('[data-id="' + pic.id + '"]')) return;
    const chip = document.createElement('span');
    chip.className = 'chat-chip';
    chip.dataset.id = pic.id;
    if (pic.thumb_url) {
      const img = document.createElement('img');
      img.src = pic.thumb_url;
      img.alt = pic.name || '';
      img.width = 48;
      img.height = 48;
      chip.appendChild(img);
    } else {
      chip.appendChild(document.createTextNode(pic.name || 'picture'));
    }
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'asset_ids';
    input.value = pic.id;
    chip.appendChild(input);
    const drop = document.createElement('button');
    drop.type = 'button';
    drop.className = 'chat-unpick';
    drop.setAttribute('aria-label', 'Remove ' + (pic.name || 'picture'));
    drop.textContent = '×';
    chip.appendChild(drop);
    chips.appendChild(chip);
    syncVision();
  }

  function togglePicker() {
    const box = $('#chat-picker');
    if (!box) return;
    box.hidden = !box.hidden;
    if (box.hidden) return;
    box.textContent = 'Loading…';
    fetch('/hx/chat/picker')
      .then((r) => r.text())
      .then((html) => { box.innerHTML = html; })
      .catch(() => { box.textContent = 'Could not load your pictures.'; });
  }

  function pick(btn) {
    const pic = { name: btn.dataset.name, thumb_url: btn.dataset.thumb };
    if (btn.dataset.chatAsset) {
      addChip({ ...pic, id: btn.dataset.chatAsset });
      return;
    }
    // a Gallery picture becomes an asset first
    btn.disabled = true;
    fetch('/chat/attach-output/' + encodeURIComponent(btn.dataset.chatOutput), { method: 'POST' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('refused'))))
      .then((asset) => addChip(asset))
      .catch(() => showError('That picture could not be attached.'))
      .finally(() => { btn.disabled = false; });
  }

  function upload(input) {
    if (!input.files || !input.files.length) return;
    const data = new FormData();
    Array.from(input.files).forEach((f) => data.append('files', f));
    const label = input.closest('label');
    if (label) label.classList.add('is-busy');
    fetch('/assets/upload', { method: 'POST', body: data, headers: { Accept: 'application/json' } })
      .then((r) => r.json())
      .then((reply) => {
        (reply.assets || []).forEach(addChip);
        showError((reply.errors || []).join(' '));
      })
      .catch(() => showError('The upload did not get through.'))
      .finally(() => { input.value = ''; if (label) label.classList.remove('is-busy'); });
  }

  // ── sending ──
  function onStart(data, retry) {
    const t = thread(), f = form();
    t.querySelectorAll('.chat-empty, .chat-unanswered').forEach((el) => el.remove());
    if (retry) {
      // the failed replies at the end are gone on the server too
      while (t.lastElementChild && t.lastElementChild.matches('.chat-assistant.chat-failed, .chat-assistant.chat-blank')) {
        t.lastElementChild.remove();
      }
    } else {
      // a failed reply further up is no longer the one waiting: its button would only be refused
      t.querySelectorAll('[data-chat-retry]').forEach((b) => b.remove());
      t.insertAdjacentHTML('beforeend', data.user_html);
      f.elements.message.value = '';
      f.elements.message.style.height = '';
      if ($('#chat-chips .chat-chip')) page().dataset.hasPictures = '1';
      $('#chat-chips').textContent = '';
      $('#chat-picker').hidden = true;
    }
    f.elements.chat_id.value = data.chat_id;
    page().dataset.chatId = data.chat_id;
    $('#chat-title').textContent = data.title;
    if (location.pathname !== data.url) history.replaceState(history.state, '', data.url);
    t.insertAdjacentHTML('beforeend',
      '<article class="chat-msg chat-assistant chat-pending" id="chat-pending">' +
      '<div class="chat-body chat-live"></div><footer><small class="muted">Writing…</small></footer></article>');
    toBottom();
    refreshList();
    syncVision();
  }

  function onDelta(data) {
    const live = $('#chat-pending .chat-live');
    if (!live) return;
    const follow = nearBottom();
    live.appendChild(document.createTextNode(data.text));
    if (follow) toBottom();
  }

  function onDone(data) {
    const pending = $('#chat-pending');
    const follow = nearBottom();
    if (!data.html) { reloadThread(); return; }
    if (pending) pending.outerHTML = data.html;
    else thread().insertAdjacentHTML('beforeend', data.html);
    if (data.total) $('#chat-total').textContent = data.total;
    if (follow) toBottom();
    refreshList();
  }

  function handle(block, retry) {
    let event = '', raw = '';
    block.split('\n').forEach((line) => {
      if (line.startsWith('event: ')) event = line.slice(7);
      else if (line.startsWith('data: ')) raw += line.slice(6);
    });
    if (!event) return;
    const data = JSON.parse(raw || '{}');
    if (event === 'start') onStart(data, retry);
    else if (event === 'delta') onDelta(data);
    else if (event === 'done') onDone(data);
  }

  async function send(retry) {
    const f = form();
    if (!f || controller) return;
    const data = new FormData(f);
    if (retry) {
      data.set('retry', '1');
      data.delete('message');
      data.delete('asset_ids');
    } else if (!String(data.get('message') || '').trim()) {
      return;
    }
    showError('');
    controller = new AbortController();
    setBusy(true);
    let stopped = false;
    try {
      const res = await fetch('/chat/send', { method: 'POST', body: data, signal: controller.signal });
      if (!res.ok || !res.body) {
        let message = 'Something went wrong.';
        try { message = (await res.json()).error || message; } catch (e) { /* not JSON */ }
        showError(message);
      } else {
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let cut;
          while ((cut = buffer.indexOf('\n\n')) >= 0) {
            handle(buffer.slice(0, cut), retry);
            buffer = buffer.slice(cut + 2);
          }
        }
      }
    } catch (e) {
      stopped = true; // Stop was pressed, or the connection dropped
      if (e.name !== 'AbortError') showError('The connection was lost. What was written so far has been kept.');
    }
    controller = null;
    setBusy(false);
    if (stopped) {
      // the server saves what was written once it notices we left; show that
      setTimeout(() => { reloadThread(); refreshList(); }, 700);
    }
  }

  // ── list: rename, delete ──
  function rename(btn) {
    const title = window.prompt('Name this conversation', btn.dataset.title || '');
    if (title === null || !title.trim()) return;
    const data = new FormData();
    data.set('title', title);
    data.set('current', chatId());
    fetch('/chat/' + btn.dataset.chatRename + '/rename', { method: 'POST', body: data })
      .then((r) => (r.ok ? r.text() : null))
      .then((html) => {
        if (!html) return;
        $('#chat-list').outerHTML = html;
        if (btn.dataset.chatRename === chatId()) $('#chat-title').textContent = title.trim();
      })
      .catch(() => {});
  }

  function remove(btn) {
    if (!window.confirm('Delete "' + (btn.dataset.title || 'this conversation') + '"? This cannot be undone.')) return;
    fetch('/chat/' + btn.dataset.chatDelete + '/delete', { method: 'POST' })
      .then((r) => {
        if (!r.ok) return;
        if (btn.dataset.chatDelete === chatId()) location.href = '/chat';
        else refreshList();
      })
      .catch(() => {});
  }

  function copy(btn) {
    const raw = btn.parentElement.querySelector('.chat-raw');
    if (!raw) return;
    const done = () => {
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    };
    // navigator.clipboard only exists on https and localhost; from another device on
    // the home network the page is plain http, so fall back to selecting the text
    const bySelection = () => {
      raw.hidden = false;
      raw.select();
      try { if (document.execCommand('copy')) done(); } catch (e) { /* nothing to do */ }
      raw.hidden = true;
      btn.focus();
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(raw.value).then(done).catch(bySelection);
    } else {
      bySelection();
    }
  }

  // ── events ──
  document.addEventListener('submit', (e) => {
    if (e.target.id !== 'chat-form') return;
    e.preventDefault();
    send(false);
  });

  document.addEventListener('click', (e) => {
    if (!page()) return;
    const hit = (sel) => e.target.closest(sel);
    let el;
    if (hit('#chat-stop')) { if (controller) controller.abort(); }
    else if (hit('#chat-attach')) togglePicker();
    else if ((el = hit('.chat-pick'))) pick(el);
    else if ((el = hit('.chat-unpick'))) { el.closest('.chat-chip').remove(); syncVision(); }
    else if (hit('[data-chat-retry]')) send(true);
    else if ((el = hit('[data-chat-copy]'))) copy(el);
    else if ((el = hit('[data-chat-rename]'))) rename(el);
    else if ((el = hit('[data-chat-delete]'))) remove(el);
  });

  document.addEventListener('change', (e) => {
    if (e.target.id === 'chat-upload') upload(e.target);
    else if (e.target.name === 'model' && e.target.closest('#chat-form')) syncVision();
  });

  document.addEventListener('keydown', (e) => {
    const box = e.target;
    if (e.key !== 'Enter' || box.name !== 'message' || !box.closest('#chat-form')) return;
    // on a touch keyboard Enter is the only way to make a new line
    if (e.shiftKey || e.isComposing || window.matchMedia('(pointer: coarse)').matches) return;
    e.preventDefault();
    send(false);
  });

  document.addEventListener('input', (e) => {
    const box = e.target;
    if (box.name !== 'message' || !box.closest('#chat-form')) return;
    box.style.height = 'auto';
    box.style.height = Math.min(box.scrollHeight + 2, window.innerHeight * 0.4) + 'px';
  });

  function init() {
    if (!page() || page().dataset.chatReady) return;
    page().dataset.chatReady = '1';
    syncVision();
    // on a phone the list sits above the conversation: start with it folded away
    const fold = $('.chat-list-fold');
    if (fold && window.matchMedia('(max-width: 767px)').matches) fold.open = false;
    if ($('#chat-thread .chat-msg')) toBottom();
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
