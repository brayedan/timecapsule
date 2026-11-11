# hermes-timecapsule

Sealed messages for later. Seal a note so it cannot be read until its
release condition is met — a date, N days of silence, or the sealer's
explicit release — with a grace ladder and slow, announced deletion.

## What it is

One capability: **a message you cannot read until it is time.** The
agent seals a message, picks how it releases, and the plugin enforces
the wait. Release runs through a grace period first, so the sealer gets
a last chance to change their mind. Deletion is slow (a countdown, not
an instant) and announced (a permanent record, not a silent wipe).

## The tools

| Tool | What it does |
| --- | --- |
| `timecapsule_seal` | Seal a message. Condition: `date` (ISO date), `silence` (N days of no activity), or `manual` (only the sealer releases it). `grace_days` (default 3, 0 = open immediately once the condition is met), `delete_days` (default 7). |
| `timecapsule_list` | Metadata ONLY — id, label, sealed-at, condition, status. Content is never shown. |
| `timecapsule_open` | Returns the content only if the condition is met and grace has ended. Otherwise refuses honestly and says why and what remains. |
| `timecapsule_delete` | Starts the deletion countdown (default 7 days). The capsule can be restored while the countdown runs. |
| `timecapsule_restore` | Brings a pending-deletion capsule back to sealed. The release condition still applies. |
| `timecapsule_reseal` | Re-seals a capsule in grace, back to sealed. Only the sealer, only during grace. |
| `timecapsule_status` | Counts by status, the permanent deletion-announcement log, last activity (the silence clock). No content. |

## Release conditions

- **date** — open after a date/time (UTC, ISO 8601, e.g. `2026-11-11T15:00:00Z`).
- **silence** — open after N days with no activity on the store
  (dead-man's-switch flavour). Activity = seal, open, delete, restore,
  reseal. Listing and status are read-only and do not reset the clock.
  A refused open attempt *is* activity — checking on a silent capsule
  proves you are still there.
- **manual** — only the sealer can release it, by calling open. Seal
  with an `author` if you want this gate to mean something; a capsule
  sealed with no author can be released by anyone who calls open.

## The grace ladder

When a release condition is met, the capsule enters **grace** for
`grace_days`. During grace the sealer can re-seal it (back to sealed)
or delete it. After grace it becomes **open**. `grace_days=0` opens
immediately once the condition is met.

## Deletion is slow and announced

`timecapsule_delete` does not delete. It marks the capsule
pending-deletion for `delete_days`. During the countdown it can be
restored. When the countdown ends, the capsule is removed and a
permanent entry is written to the deletion log
(`deletions[]` in `store.json`), visible in `timecapsule_status`.
Deleted ids are never reused.

## What the seal is — and is not

**The seal is access control, not cryptography.** The store holds
content in plaintext in one JSON file (`store.json` in the plugin's
data directory), and the code paths enforce who can read it and when.
Anyone with direct filesystem access to the store file can read it.
Do not seal anything that must survive a hostile reader of your own
disk. See `SECURITY.md` for the full threat model.

## Install

```powershell
# From the Hermes plugins directory on the machine running Hermes:
hermes plugins validate timecapsule
hermes plugins doctor timecapsule
hermes plugins enable timecapsule
```

Run `validate` from the plugins directory — it resolves the plugin
name relative to the current directory. No dependencies, no network,
no credentials, no privileged capabilities. Stdlib only.

Config (optional): `timecapsule_root` — directory holding `store.json`.
Empty = the plugin's own data directory
(`plugin-data/timecapsule/`).

## Limits

Message ≤ 20,000 chars, label ≤ 100 chars, at most 100 capsules,
grace 0–30 days, deletion countdown 1–30 days, silence 1–365 days.

## Tests

```bash
python -m unittest discover -s tests -v
```

20 tests, stdlib `unittest` (no pytest — keeps the stdlib-only
doctrine). A fake clock drives the time-dependent behaviour, so the
suite runs in milliseconds instead of days.

## Demo

`demo-script.md` — a <90s script: seal a capsule, try to open it
early (refused), release it, open it, then delete a second capsule
and show the announcement.

## Dashboard tab

**Time Capsule** (path `/timecapsule`, Sunface dark/gold) — the human
experience layer, reading the same `store.json` the tools use. No
second copy of the state.

- A capsule list: label, status, sealed date, release condition, and
  the grace / deletion countdown where one is running.
- A detail view: metadata for sealed capsules; the content only for
  capsules whose status is **open**. A sealed capsule's content is
  never shown on the tab.
- A seal form: label, message, condition (date / silence / manual),
  grace days, deletion countdown, author.
- Actions the state allows, and only those: **Release** on a sealed
  manual capsule (as the sealer), **Re-seal** in grace, **Restore**
  while pending deletion, **Delete** (slow, announced). No Release
  button on a sealed date or silence capsule whose condition has not
  arrived.
- Status counts and the permanent deletion-announcement log.

**The seal is access control, not cryptography** — the tab states
this on its face. The content sits in plaintext in `store.json`; the
tab enforces who can read it and when, the same as the tools. See
`SECURITY.md`.

## Known gaps (v0.1.0)

- **No encryption.** See "What the seal is" above. A crypto-sealed
  v0.2.0 (per-capsule key, sealed blob) is a real follow-up, not a
  claim made here.
- **No hooks.** Nothing fires on release; the agent polls with
  `timecapsule_list` / `timecapsule_status`.
