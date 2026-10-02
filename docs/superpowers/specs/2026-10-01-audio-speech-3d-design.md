# Music & SFX, Speech and 3D — design (2026-10-01)

Approved in conversation on 2026-10-01: three new generation modules beside Image and
Video, reached as tabs on the Generate page, with a bundled rotatable 3D viewer.

## Goal

Generate **music and sound effects**, **speech (text-to-speech)** and **3D objects** from
VJHStudio with the same queue, projects, cost tracking, gallery and backups that images
and videos use. Text-to-speech is its own module (own tab, own form, own model list),
not a mode of the music form.

## What RunWare offers (public catalog, 2026-10-01)

- `audioInference`, category `audio`, 20 models. Music: MiniMax Music 2.6 ($0.15 a song),
  ACE-Step v1.5 ×5 (~$0.0001/s), Seed Audio ($0.0026/s). Sound effects: Mirelo SFX 1.5.
  Speech: Inworld TTS ×3, xAI TTS, MiniMax Speech 2.8, Gemini 3.1 Flash TTS, Fish Audio,
  Qwen3-TTS ×3, Dia2 (priced per character, UTF-8 byte or token). Reply rows carry
  `audioURL`, `audioUUID`, `cost`. Formats MP3 (default), WAV, FLAC, OGG.
- `3dInference`, category `3d`, 7 models: Tripo 3.1, Hunyuan 3D 3.1 Pro/Rapid, Meshy-6,
  Rodin Gen-2 (text or image), TRELLIS.2 and SAM 3D (image only). Priced per generation
  ($0.225–$0.80, add-ons extra). Output GLB (Tripo also FBX). Reply rows carry
  `outputs: {…}`; its inner shape is not documented and is read defensively (first
  http(s) URL found, preferring one that ends in the requested format) until a live run
  pins it.
- Every model's parameters differ and are listed on its docs page
  (`runware.ai/docs/models/<slug>`): `positivePrompt`, `duration` range, `seed`,
  `settings.lyrics`, `settings.instrumental`, `speech.text`, `speech.voice` (30–133
  values), `speech.language`, `speech.speed`, `settings.pbr`, `inputs.image` / `inputs.images`…

## Kinds

Three new values for `catalog_models.kind`, `jobs.kind`, `outputs.kind` (all `VARCHAR(8)`,
no migration): `audio` (music & sound effects), `speech`, `3d`.

RunWare's `audio` category is split by us. A model is `speech` when its harvested fields
contain `speech.text`; before a harvest, when a price rate is per `character`, `utf8Byte`
or token, or its name/slug says TTS, Speech or Dia. Otherwise it is `audio`.

A model is generate-capable when: `audio`/`speech` — it has `io:text-to-audio`;
`3d` — it has `io:text-to-3d` or `io:image-to-3d`. The existing rule still applies:
a required input the app cannot supply (audio track, video, reference video/audio)
hides the model with a badge. An input *image* is suppliable (from Assets).

## Catalog, prices, constraints

- Refresh prices lists categories `image`, `video`, `text`, `audio`, `3d`.
- Price units: `per_output` (rate unit `output`: a song, a 3D object; primary = lowest
  amount), `per_second` (`durationSecond`, or "$X per second" in `pricingOverview` when
  no rate row exists — ACE-Step), `per_1k_chars` (`character` / `utf8Byte` × 1000).
  Token-priced speech has no primary price ("price unknown") until a run reports cost.
- The docs harvest stores every non-plumbing parameter of a model under
  `constraints_json.fields` (`{name: {type, min, max, step, default, values, required}}`),
  alongside the existing `inputs`. The forms are rendered from `fields`.
- The three kinds get docs pages only; the API probes (built for image/video shapes)
  are never sent for them.
- `curated_models.json` ships the 27 models with their `fields`, `inputs` and prices so a
  fresh install works before any refresh or harvest.

## Generate page

Five tabs: Image, Video, Music & SFX, Speech, 3D. Image and Video keep the existing
Alpine form. The three new tabs are links to `/generate/audio`, `/generate/speech`,
`/generate/3d`, which render the same page shell (tabs, project picker, cost estimate,
queue panel) around a plain server-rendered form; on those pages Image and Video are
links back. Changing the model re-renders the fields for that model.

- **Music & SFX**: description (required); lyrics and an instrumental switch when the
  model lists them; length when the model takes `duration` (its own min/max/default);
  format; seed.
- **Speech**: the text to read (required, the model's own max length shown); voice
  (the model's list, a type-to-filter box); language; speed when offered; format.
- **3D**: prompt when the model does text-to-3D; one image from Assets when it does
  image-to-3D (required when it is image-only, or when no prompt is given); format.
- Other one-level `settings.*` booleans, choices and numbers the model lists appear
  under "More settings"; nested ones stay reachable through the extra-JSON box.

Pre-flight refuses, before a job row exists: an unsuppliable input, a missing prompt
or text, a 3D request with neither prompt nor image, an image on a text-only model.

Estimates: per second × length; per output × 1; per 1k characters × the text's length;
"n/a" when the model has no price.

## Pipeline

`schemas/media.py` (`AudioRequest`, `SpeechRequest`, `Model3DRequest`),
`runware/tasks_media.py` (pure builders), the existing runner (its free validation
retries apply unchanged), `results.parse_items` (adds `audioURL` and the 3D `outputs`
walk), the existing downloader (extension from the output format), `outputs.prepare`
without thumbnail or poster. Audio length is recorded from the task's `duration` when
one was sent. Costs are recorded per row as today. These jobs do not enter the prompt
library (its rows are image/video prompt forms).

## Gallery

- Music and speech cards: an audio player and a details button.
- 3D cards: a 3D icon that opens the details panel, where Google's `<model-viewer>`
  (vendored under `static/vendor`, Apache-2.0, loaded only when a 3D object is opened)
  shows a model that can be rotated and zoomed; Download gives the GLB.
- The kind filter lists the five kinds. "Use as reference" stays image-only; the size
  row is shown only for images and videos; Remix opens the matching tab pre-filled.

## Models page

Three more collapsible sections: Music & SFX, Speech, 3D.

## Out of scope

MCP tools for the new kinds; uploading audio/video/mesh files as inputs (covers, voice
cloning, sound for a video, re-texturing a mesh); multi-speaker speech; a settings UI
for per-kind default models.

## Testing

Fake-client tests for builders, pricing, classification, pre-flight, jobs end to end,
pages and gallery. One paid live run per module, each only with the owner's go-ahead:
ACE-Step Turbo (< $0.01), xAI TTS (< $0.01), Hunyuan 3D Rapid ($0.225).

## Amendments after first live use (2026-10-01)

- **3D is text or image, never both.** Hunyuan's docs say "Provide exactly one of:
  positivePrompt, inputs.image"; a job sent with both lost its image to a free retry
  and rendered the text. `Model3DRequest` refuses both; a model that does both gets a
  "From a description / From an image" choice, only the chosen box is shown (CSS on the
  checked radio) and only it is sent (`media_forms.parse` reads `source`).
- **Provider failures carry their reason.** RunWare's `"<provider> responded with HTTP
  . Additional information below."` keeps the information in `responseContent`, which
  the SDK drops. `runware/details.py` asks for the task's stored reply (`getResponse`,
  free) and the job card shows it, with a plain-words hint for
  `RequestLimitExceeded.JobNumExceed` (Hunyuan runs a limited number of an account's
  jobs at once, and a job the user stopped waiting for is still running there).
- **Cancel wording.** A stopped non-image job says it may still finish and be billed.
- **htmx inheritance.** Anything inside `#media-form` that issues its own request names
  its own `hx-target`; the estimate once inherited the form's and replaced the queue
  panel, which killed the Generate button.
- **Vocals and lyrics (MiniMax Music 2.6).** Its docs: "When none of settings.instrumental
  or settings.lyricsOptimizer are provided, settings.lyrics is required", "when
  instrumental is true, lyrics cannot be used / lyricsOptimizer cannot be true". A prompt
  alone was rejected (`Missing required parameter: '[settings][lyrics]'`). A model that
  lists `settings.instrumental` gets a Vocals select (instrumental — the default —, my
  lyrics, the model writes them); `tasks_media.resolve_audio` completes the request (no
  lyrics → instrumental; instrumental → lyrics and the writer left out), raises
  `NEEDS_LYRICS` for vocals-with-my-lyrics and none typed (nothing is sent), and the
  resolved request is what the job stores. `instrumental` and `lyricsOptimizer` are only
  ever sent as `true`. Models without the switch (ACE-Step) are untouched.
- **Seeds.** Ranges differ per model (MiniMax Music 0..1000000, ACE-Step 0..2147483647,
  Tripo 1..20240919) and a seed travels with a remix, a retry or a model change.
  `tasks_media.fold_seed` maps an out-of-range seed into the model's range by modulo
  (deterministic), `tasks_media.resolve` applies it before the job is stored, and
  `runner.range_correction` does the same from RunWare's "must be … between A and B"
  for a model with nothing harvested (other numbers are clamped).
