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

  var api = { ORDER: ORDER, clean: clean, composePrompt: composePrompt, buildNegative: buildNegative };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (typeof global !== 'undefined') {
    global.ORDER = ORDER;
    global.clean = clean;
    global.composePrompt = composePrompt;
    global.buildNegative = buildNegative;
  }
})(typeof window !== 'undefined' ? window : globalThis);
