# Chat page: design

Date: 2026-10-03. Approved in conversation ("yes") after three choices by the user:
general chat only (no link to the Generate tabs), a saved list of conversations, and
pictures from Assets and Gallery.

## Purpose

A plain chat with any of RunWare's text models, inside VJHStudio, for a single household on
a trusted network. Success: pick a model, type, read the reply as it is written, come back
to the conversation later, and see what it cost.

## What the user sees

- **Chat** in the top menu, between Gallery and Prompts. `/chat` is a new, empty conversation;
  `/chat/<id>` is a saved one.
- A list of conversations at the side, newest first; on a narrow screen it folds away above
  the conversation. Each can be renamed or deleted. A conversation is created by its first
  message and titled from it (first line, 60 characters).
- The composer: message box, model dropdown, **Attach picture**, **Send**. Enter sends on a
  desktop; Shift+Enter (and Enter on a touch screen) makes a new line. An optional
  **Instruction** box holds a standing instruction for the conversation.
- The reply appears piece by piece. **Stop** ends it; what was written is kept and marked
  "stopped". Leaving the page stops it too.
- Each reply shows its model and cost; the conversation shows a running total. Replies are
  formatted (headings, lists, tables, code); each has **Copy**.
- A failed reply shows the error in place, and the last one offers **Try again**. The
  user's message is never lost.

## Models and pictures

- The dropdown lists the catalog's text models that hold a conversation: `io:text-to-text`,
  no `op:` tag (that excludes captioners, age detectors and the prompt enhancer), not hidden.
  A text model with no capability tags at all (added from search) is allowed.
- `defaults.chat_model` (Settings), default `anthropic:claude@haiku-4.5` (added to the shipped
  model list, whose version stays 4: a bump would re-seed every shipped row over what Refresh
  prices learned); falls back to the first model in the list. A conversation remembers the model last used in it.
- **Attach picture** shows only on models tagged `io:image-to-text`. The picker offers image
  assets, the most recent Gallery images (picking one imports it as an asset, deduplicated by
  content, as "use as reference" already does) and an upload (stored in Assets).

## What RunWare does (checked live 2026-10-03, $0.0009)

- `textInference` with `messages` (roles `user` / `assistant`, last one `user`),
  `settings.systemPrompt`, `includeCost`, `includeUsage`. The SDK's `client.stream()` yields
  text pieces and a final result with `cost`, `usage`, `finish_reason`.
- Pictures are a task-level `inputs.images` list, not part of a message, and are **not
  remembered**: a second turn without them answers "No". So every send carries every picture
  of the conversation (uploaded once each; `assets.media_map` caches the UUID).
- A model that cannot see pictures refuses `inputs.images` (free validation error), so they
  are simply not sent to such a model.
- `settings.maxTokens` is **not sent**: with 300, GPT-5 nano spent it all on reasoning and
  returned no text (`finishReason: length`). An empty reply is shown as an error with Try again.

## How it works

- Tables `chats` (title, model_air, system_prompt, timestamps) and `chat_messages` (chat_id,
  role, content, asset_ids_json, model_air, cost, prompt_tokens, completion_tokens,
  finish_reason, error, created_at). Migration 0007. Deleting a chat deletes its messages.
- `POST /chat/send` saves the user's message, then answers with a `text/event-stream` body:
  `start` (chat id, URL, the rendered user message), `delta` (text), `done` (the rendered
  reply, the running total). It is a POST read with `fetch`, never an `EventSource`: a
  dropped connection must not silently re-send a billed request.
- The reply is produced by a server task that is independent of the HTTP response; the
  response only relays its events. When the browser goes away the response sets the task's
  cancel flag; the task saves what it has and closes the client normally.
- History sent = every message in order, skipping replies that failed or are empty, with
  consecutive user messages joined (so a retried message is sent once and the list still
  ends on `user`).
- Cost: `costs.record_usage(task_type="textInference", …)` per reply, so Home and Settings
  spending include chat. A stopped reply reports no cost (RunWare sends it with the final
  piece, which never arrives); it is stored as unknown.
- Markdown is rendered on the server by `markdown-it-py` (new dependency, pure Python) with
  raw HTML and images off, links opened in a new tab. While streaming, the text is plain.

## Errors

No API key: the usual "add your key" message, nothing sent. Empty message, unknown chat or
model: refused before anything is saved or sent. RunWare errors are worded by
`errors.classify` and stored on the reply.

## Not in this version

Tools or web search, documents/video/audio attachments, editing earlier messages, export,
per-project chats, chat over MCP, a max-length or thinking-level control.

## Testing

Unit tests for the task builder, history, model filter and Markdown safety; route tests with
the fake client (stream added to `FakeRunware`) for send, new chat, pictures, errors, stop,
retry, rename, delete, cost recording; a browser run on a scratch instance (dummy key) for
the page mechanics; one real send by the user on their own instance.
