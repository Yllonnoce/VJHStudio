/* Client-side mirror of vjhstudio/services/prompts.py — kept byte-for-byte in sync with
 * `clean`, `compose` (here `composePrompt`) and `build_negative` (here `buildNegative`).
 * Works both as a plain browser <script> (exposes globals) and as a CommonJS module
 * (`tests/test_js_mirror.py` requires() it under node). Do not add browser-only APIs here.
 */
(function (global) {
  'use strict';

  // Mirrors services/prompts.py::FIELD_ORDER. `negative` is deliberately excluded —
  // it is composed separately by buildNegative(), exactly as in the Python source.
  var ORDER = ['subject', 'style', 'mood', 'lighting', 'camera', 'composition', 'colour', 'extras'];

  // Mirrors services/prompts.py::clean() exactly.
  var clean = function (s) {
    return (s ?? '').replace(/\s+/g, ' ').trim().replace(/[ ,;.]+$/, '').trim();
  };

  function _dedupeJoin(tokens) {
    var out = [];
    var seen = {};
    for (var i = 0; i < tokens.length; i++) {
      var t = tokens[i];
      var key = t.toLowerCase();
      if (!Object.prototype.hasOwnProperty.call(seen, key)) {
        seen[key] = true;
        out.push(t);
      }
    }
    return out.join(', ');
  }

  // Mirrors services/prompts.py::compose().
  function composePrompt(fields) {
    fields = fields || {};
    var parts = [];
    for (var i = 0; i < ORDER.length; i++) {
      var v = clean(fields[ORDER[i]]);
      if (v) parts.push(v);
    }
    return _dedupeJoin(parts);
  }

  // Mirrors services/prompts.py::_tokens(): split on commas, clean each token, drop blanks.
  function _tokens(text) {
    return (text || '')
      .split(',')
      .map(clean)
      .filter(function (t) {
        return !!t;
      });
  }

  // Mirrors services/prompts.py::build_negative(). `userNegative`, `defaultNegative` and
  // `noTextTokens` are raw comma-separated strings, exactly like PromptForm.negative and
  // the `defaults.negative_prompt` setting.
  function buildNegative(userNegative, defaultNegative, useDefault, noText, noTextTokens) {
    var toks = _tokens(userNegative);
    if (useDefault) toks = toks.concat(_tokens(defaultNegative));
    if (noText) toks = toks.concat(_tokens(noTextTokens));
    return _dedupeJoin(toks);
  }

  // htmx 2.x wraps a non-object `HX-Trigger` payload (our job-finished events are a JSON
  // array) as `event.detail = {value: [...], elt: ...}` rather than handing the array
  // straight through as `event.detail`. This normalises every shape a trigger payload
  // could arrive in back to a plain array, so callers never need to know which one htmx
  // chose: an array (already unwrapped, or a future non-htmx caller), `{value: [...]}}`
  // (htmx's actual wrapping of an array payload), a bare object (a single event, not a
  // list), or a missing/null detail (defensive default).
  function vjhUnwrapTrigger(detail) {
    if (detail && Array.isArray(detail.value)) return detail.value;
    if (Array.isArray(detail)) return detail;
    if (detail) return [detail];
    return [];
  }

  // Turns a "Use this" click on a polish card into the payload generateForm.usePolish()
  // stores: `polishJsonText` is the raw text of the swapped-in `#polish-json` <script>
  // (generate/_polish_results.html renders PolishResult.to_json(), whose own
  // `chosen_index` is always null there); `index`/`text` come off the clicked button's
  // `data-index`/`data-text`, `mode`/`model` off its `data-mode`/`data-model` (so the
  // model/mode are still known even if the script tag is missing or malformed -- only
  // `versions`/`cost`, which are identical for every card, have to come from the blob).
  // Pulled out as a pure function, mirroring composePrompt/buildNegative, so it gets a
  // node test instead of only being reachable from a live DOM click.
  function vjhChoosePolish(polishJsonText, index, text, mode, model) {
    var blob = {};
    try {
      blob = JSON.parse(polishJsonText || '{}') || {};
    } catch (e) {
      blob = {};
    }
    return {
      text: text || '',
      polishJson: JSON.stringify({
        source: mode || blob.mode || '',
        model: model || blob.model || '',
        versions: Array.isArray(blob.versions) ? blob.versions : [],
        chosen_index: index,
        cost: typeof blob.cost === 'number' ? blob.cost : 0,
      }),
    };
  }

  // ── Idea chips ─────────────────────────────────────────────────────────────
  // A chip toggles one phrase inside a plain comma-separated builder field, leaving
  // everything the user typed alone. Pure string work (no DOM), so it is testable under
  // node like composePrompt; app.js's generateForm.toggleIdea() is a one-line wrapper.
  // Deliberately *not* mirrored from prompts.py: this is UI convenience, the server
  // still receives (and composes) exactly the text the field ends up holding.
  function _splitPhrases(text) {
    return String(text || '')
      .split(',')
      .map(function (s) {
        return s.trim();
      })
      .filter(Boolean);
  }

  function vjhHasIdea(text, phrase) {
    var p = String(phrase || '').trim().toLowerCase();
    if (!p) return false;
    return _splitPhrases(text).some(function (t) {
      return t.toLowerCase() === p;
    });
  }

  function vjhToggleIdea(text, phrase) {
    var p = String(phrase || '').trim();
    if (!p) return String(text || '');
    var parts = _splitPhrases(text);
    var kept = parts.filter(function (t) {
      return t.toLowerCase() !== p.toLowerCase();
    });
    if (kept.length !== parts.length) return kept.join(', ');
    return parts.concat([p]).join(', ');
  }

  // Grow a textarea to fit its content, capped at 12 rows. `field-sizing: content` does
  // this in CSS where it is supported; this is the fallback for everywhere else, and it
  // is a no-op off a textarea (and under node, where there is no DOM at all).
  function vjhAutosize(el) {
    if (!el || el.tagName !== 'TEXTAREA') return;
    el.style.height = 'auto';
    var lh = parseFloat(getComputedStyle(el).lineHeight || '20') || 20;
    el.style.height = Math.min(el.scrollHeight, 12 * lh) + 'px';
  }

  var api = {
    ORDER: ORDER,
    clean: clean,
    composePrompt: composePrompt,
    buildNegative: buildNegative,
    vjhUnwrapTrigger: vjhUnwrapTrigger,
    vjhChoosePolish: vjhChoosePolish,
    vjhHasIdea: vjhHasIdea,
    vjhToggleIdea: vjhToggleIdea,
    vjhAutosize: vjhAutosize,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (typeof global !== 'undefined') {
    global.ORDER = ORDER;
    global.clean = clean;
    global.composePrompt = composePrompt;
    global.buildNegative = buildNegative;
    global.vjhUnwrapTrigger = vjhUnwrapTrigger;
    global.vjhChoosePolish = vjhChoosePolish;
    global.vjhHasIdea = vjhHasIdea;
    global.vjhToggleIdea = vjhToggleIdea;
    global.vjhAutosize = vjhAutosize;
  }
})(typeof window !== 'undefined' ? window : globalThis);
