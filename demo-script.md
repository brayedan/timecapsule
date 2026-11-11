# Demo script — hermes-timecapsule (<90s)

**Setup (before recording):** plugin installed and enabled on Arca.
Run `hermes plugins disable timecapsule` + `hermes plugins enable
timecapsule` first — the gateway drops toolsets and `-t` goes stale
otherwise (the town-square lesson). Use model
`mistralai/mistral-small-3.1-24b-instruct` (verified working on Arca).
One-shot sessions: `hermes -z "..." -t timecapsule`.

**Gate checklist:** install ✓ · real demo (this script) · security
review ✓ (`SECURITY.md`) · release readiness (README + this script) ·
owner eyes-on ☐.

## Shot 1 — Seal it (0–25s)

Narration: "A message for later. I seal it to open after the Forge
deadline — and I seal a second one that only I can release."

Commands (one-shot session 1):

```
hermes -z "Seal a capsule with message 'Forge result: we won, and the gate held' with release_condition date, release_date 2026-11-11T15:00:00Z, grace_days 0, label 'forge-result', author hyperion. Then seal a second capsule with message 'The sealer's note' with release_condition manual, grace_days 0, author hyperion, label 'sealer-note'. Report both capsule ids." -t timecapsule
```

Expected: `cap-0001` sealed (date 2026-11-11T15:00:00Z), `cap-0002`
sealed (manual). Neither call shows the content back.

## Shot 2 — Try to open early: refused (25–50s)

Narration: "It is not time. The plugin refuses — and tells me
exactly why."

```
hermes -z "Open cap-0001. Then list the capsules and show me the metadata only." -t timecapsule
```

Expected: open refuses — "sealed until 2026-11-11T15:00:00Z".
The list shows id, label, condition, status — and no content
anywhere on screen.

## Shot 3 — Release and open (50–75s)

Narration: "The manual capsule is mine to release. I open it — and
there is the message, only now."

```
hermes -z "Open cap-0002 as author hyperion. Then show the store status." -t timecapsule
```

Expected: `{"ok": true, "content": "The sealer's note", ...}` and
status counts `open: 1, sealed: 1`.

## Shot 4 — Delete is slow and announced (75–89s)

Narration: "Deletion is not a wipe. It counts down — and when it
ends, the deletion is announced forever."

```
hermes -z "Delete cap-0001 with delete_days 1. Show the status, and confirm it is pending-deletion, not gone." -t timecapsule
```

Expected: `status: pending-deletion, delete_after: <tomorrow>`,
note "restore within 1 days or it is deleted and announced".
(The full countdown-expiry announcement is covered by the unit test
`test_delete_countdown_expiry_announces`, which advances a fake clock
1 day and shows the capsule gone and the deletion logged — a real
24-hour wait is not demoable in 90 seconds, and the test is the
honest evidence for that beat.)

## What this proves

1. Seal works (two conditions, two capsules).
2. Sealed means sealed — early open is refused with the reason.
3. List shows metadata only, never content.
4. Release works — manual open by the sealer returns the content.
5. Deletion is a countdown, not an instant, and the announcement
   mechanism is exercised by the test suite.

## Tab eyes-on (separate step — not in the 90s)

The four shots above already run 89s, so the dashboard tab is its
own eyes-on, not a fifth shot. Open the **Time Capsule** tab
(`/timecapsule`):

1. `cap-0001` ("forge-result", sealed to 2026-11-11T15:00:00Z) shows
   metadata only — label, status, condition, countdown — and no
   content, and no Release button.
2. `cap-0002` ("sealer-note", open) shows its content in the detail
   view.
3. Seal a capsule from the form (date condition, grace 0) and watch
   it appear in the list.
4. The header states the seal is access control, not cryptography.
