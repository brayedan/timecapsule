# Time discipline

You have a time capsule — a place to seal messages that must not be
read until it is time. Use it for things the future-you (or a future
agent) needs, not for things the present needs to act on.

## The tools

- `timecapsule_seal` — seal a message. Pick the release condition:
  `date` (ISO date), `silence` (N days of no activity), or `manual`
  (only you release it). `grace_days` defaults to 3 — the sealer's
  last chance to change their mind after the condition is met. Set
  `grace_days: 0` when you want it to open the moment the condition
  is met.
- `timecapsule_list` — metadata only. Never shows content. Check this
  before assuming a capsule is ready.
- `timecapsule_open` — returns the content only when the condition is
  met and grace has ended. If it refuses, believe the refusal: the
  message says why and what remains.
- `timecapsule_delete` — starts a slow deletion countdown (default 7
  days). It is not instant, and the deletion is announced. Use
  `timecapsule_restore` while the countdown runs if you change your
  mind.
- `timecapsule_reseal` — re-seal a capsule in grace, back to sealed.
  Only you (the sealer), only during grace.
- `timecapsule_status` — counts by status, the deletion-announcement
  log, and the last activity timestamp (the silence clock).

## Rules

1. **Seal with an author.** Manual-release capsules only gate on the
   sealer when you pass `author`. No author, no gate.
2. **Silence means silence.** Any seal, open, delete, restore, or
   reseal resets the silence clock — including a refused open attempt.
   Do not poke a silent capsule to "check" on it; the poke is activity.
   Listing and status are read-only and safe to check.
3. **Grace is the last chance.** When a condition is met, the capsule
   sits in grace for `grace_days`. If you sealed something you now
   regret, re-seal it during grace. After grace it opens.
4. **Deletion is slow on purpose.** If you delete a capsule by
   mistake, restore it within the countdown. When the countdown ends
   it is gone and the deletion is announced permanently.
5. **Do not seal secrets that must survive a disk reader.** The seal
   is access control, not encryption — anyone who can read
   `store.json` directly can read the content. Seal intentions,
   messages, and plans, not credentials.
6. **Do not seal what the present needs.** A capsule is a message to
   the future. If an agent needs the information now, say it plainly
   instead.
7. **The dashboard tab exists and shows the same store.** Open the
   Time Capsule tab to see capsules (metadata only until open), seal
   from the form, and watch the countdowns. Sealed content is never
   shown on the tab — the tab is a window, not a second store.
