# Security review — timecapsule 0.1.0

Reviewed 2026-11-08 (Hyperion) against the source in this directory
(`timecapsule_engine.py`, `tools.py`, `schemas.py`). Dashboard tab
(`dashboard/plugin_api.py`, `dashboard/dist/index.js`) reviewed
2026-11-09 (Hyperion). Method: read every file-access and input path
in the engine, then verified the checks with the unit suite
(`python -m unittest discover -s tests -v`, 20 tests). No external
audit; this is a self-review for the submission gate (post-0002).

## What the plugin is

A local, sealed-message store. Seven tools (seal, list, open, delete,
restore, reseal, status) over one JSON file (`store.json`) in the
plugin's data directory. Python stdlib only, no network calls, no
credentials, no third-party dependencies, no subprocesses. It runs
in-process with Hermes, i.e. with the user's own privileges — Hermes
plugin capabilities are consent, not a sandbox, so the review is about
what the code actually touches, not about an isolation boundary that
does not exist.

## The central limitation — read this first

**The seal is access control, not cryptography.** `store.json` holds
capsule content in plaintext, and the seal is enforced by the code
paths (list/status never read content; open refuses until the condition
is met). Anyone with direct filesystem read access to the store file
can read every capsule. This is stated in the README, in the engine
docstring, and in the skill. Do not seal anything that must survive a
hostile reader of your own disk. A crypto-sealed v0.2.0 (per-capsule
key, sealed blob) is a real follow-up — it is not claimed here.

## Findings

None found that block the gate. Two design notes below are not
findings, but the operator should know them.

## What was checked and passed

- **Scope of file access.** Every `open`/`read_text`/`write_text` in
  the engine resolves under `self.root`: `store.json` and the
  `.timecapsule.lock` lockfile, nothing else. The root comes from the
  plugin config (`timecapsule_root`) or `plugin_data_dir("timecapsule")`
  — never from a tool argument. No absolute paths, no reads outside
  the root.
- **Capsule ids are validated, never used as paths.** `open`/`delete`/
  `restore`/`reseal` reject anything not matching `cap-\d{4,}` before
  touching the store (`invalid capsule id`). Ids are keys into the
  JSON store, never path components — there is no traversal surface
  at all. Verified: `../../../etc`, `cap-1`, `cap-abc`, empty, and
  `None` all rejected; `cap-9999` reports "no such capsule".
- **Content never leaks through list/status.** `list_capsules()` and
  `status()` return `_public()` metadata only (id, label, sealed-by,
  sealed-at, condition, status, timestamps). The message field is
  never in the returned dict. Verified by test: the JSON blob of a
  list over two sealed capsules contains neither message.
- **Date parsing is safe.** `release_date` goes through
  `datetime.fromisoformat` (with a `Z` -> `+00:00` normalisation);
  unparseable input raises `CapsuleError`, never a crash and never a
  default that would open a capsule early. A past date is allowed
  deliberately (it means "release immediately, subject to grace") and
  is documented.
- **Bounds.** Message 1..20,000 chars, label <= 100 chars, at most
  100 capsules in the store, grace 0..30 days, deletion countdown
  1..30 days, silence 1..365 days. All enforced in `seal()` before
  the lock. Oversized input returns a clean error, not a truncated
  write.
- **Concurrency.** Mutations take `_FileLock` (O_EXCL lockfile): 10s
  wait timeout, then `CapsuleError("timecapsule store is locked by
  another writer")` — never hangs. A lock older than 30s is presumed
  stale (crashed holder) and broken. Writes are atomic (`tmp` +
  `os.replace`), so a crash mid-write leaves the old file intact,
  never a torn one. State advances (sealed -> grace -> open,
  pending-deletion -> announced) happen lazily inside the same lock
  as the operation that observes them, so two concurrent callers
  cannot double-advance a capsule.
- **Corrupt data fails closed.** A `store.json` that is not valid
  JSON, or has an unexpected shape, raises `CapsuleError("store.json
  is corrupt — refusing to touch it")` on every operation. The plugin
  never overwrites, repairs, or deletes a corrupt store on its own —
  that is a human decision. Verified by test.
- **Deletion is real and announced.** When a deletion countdown
  expires, the capsule is removed from `capsules[]` and a permanent
  entry is appended to `deletions[]` (id, label, sealer, sealed-at,
  deleted-at, reason). The log is never pruned. Deleted ids are never
  reused (`next_id` is monotonic and persisted). Verified by test.
- **Errors.** Tool failures return `{"ok": false, "error": ...}` —
  the message only, no stack traces, no paths beyond the store, no
  environment data. Refusals from `open` reveal metadata (the
  condition, the grace end, the last activity time) but never content.
- **No secrets in scope.** The plugin never reads `.env`, auth
  files, credentials, or any path outside its root. It holds no
  tokens and makes no network calls, so there is nothing to leak in
  transit.

## Dashboard tab (added 2026-11-09)

The tab (`dashboard/`) is a human layer over the same store, not a
second mechanism. What was checked:

- **Sealed content never leaves the store.** The show route
  (`GET /capsules/{id}`) returns the message only when the capsule's
  status is `open`; every other status returns metadata only
  (`content: null`). The list route returns `_public()` metadata,
  exactly as `timecapsule_list` does. The tab's JS renders content
  only when the response carries it — there is no client-side path
  to a sealed message.
- **Viewing is read-only — and that is deliberate.** The show route
  does NOT call `open_capsule`, because that method records activity
  (it resets the silence clock) and advances a sealed manual capsule
  into grace. Instead it uses the engine's own `_load` / `_refresh` /
  `_find` under `_FileLock` — the same lazy refresh `list`/`status`
  do, but no `_touch`. Looking at a silent capsule does not prove you
  are still there. No state-machine logic is reimplemented in the API
  layer; the only engine calls are the engine's own.
- **Actions go through the engine.** Seal, open, delete, restore,
  reseal all call the engine's public methods, so every gate the
  tools enforce applies identically on the tab: only the sealer
  releases a manual capsule, grace must end before an open returns
  content, deletion is a countdown with restore, ids are validated
  (`cap-\d{4,}`) before any store access. The tab's buttons are
  state-gated (no Release button on a sealed date/silence capsule,
  no Re-seal outside grace, no Restore outside pending-deletion), but
  the gating is a convenience — the engine is the authority, and a
  forged request to the API gets the same honest refusal the tools
  give.
- **No new attack surface beyond the existing tools.** The API reads
  and writes the same `store.json` under the same root, under the
  same `_FileLock`, with the same atomic writes and the same corrupt-
  store refusal. The JS is a static file with no build step and no
  third-party script. The tab runs in the dashboard with the user's
  own privileges, under the same in-process trust model as the
  plugin itself — it does not widen who can read the store, it only
  gives the owner a window onto it.
- **The limitation is stated on the tab itself.** The header carries
  the access-control-not-cryptography line, so a human looking at the
  tab is told the same thing the README, the engine docstring, the
  skill, and this review say.

## By design — not findings, but the operator should know

- **Manual release with no author has no gate.** A capsule sealed
  with no `author` can be released by anyone who calls open. Seal
  with an author if the manual gate should mean something. The skill
  states this as rule 1.
- **A refused open is activity.** Any seal, open, delete, restore,
  or reseal call updates `last_activity_at` — including an open that
  is refused. Poking a silent capsule proves you are still there and
  restarts its silence clock. Listing and status are read-only and do
  not. This is the documented semantics, and the suite tests it.
- **Read-only tools advance state.** `list` and `status` take the
  lock and run the same lazy refresh as the mutating tools, so a
  capsule whose grace expired reads as `open` rather than stale
  `grace`. The refresh is a state advance, not a user action.
- **In-process trust.** The plugin runs with the user's privileges.
  Nothing in it reaches outside the store root, but that is a property
  of this code, not a sandbox guarantee. Do not install untrusted
  forks of it.

## Test evidence (2026-11-08, local run)

```
Ran 20 tests in 0.018s — OK
```

Covering: seal/list never show content, open-before-date refuses,
open-after-date works, the grace ladder (grace -> reseal by the wrong
sealer refused -> reseal by the sealer -> grace again -> open),
silence opens after quiet and activity resets the clock, manual
release only for the sealer, delete -> pending -> restore,
delete-countdown expiry announces and the id is never reused, all
bounds, id validation, the 100-capsule cap, corrupt-store refusal,
and status counts.

## Verdict

No findings block the gate on security grounds. The one honest limit
— the seal is access control, not encryption — is stated in the
README, the engine docstring, the skill, and this review. Do not
represent it otherwise.
