# Changelog

## 0.1.1 — 2026-11-15 (unreleased)

Fix batch, one commit per item, suite green after each (42 tests).
Stdlib-only and all tool signatures unchanged.

- **#1** `seal()` refreshes before the 100-capsule cap check — expired
  pending-deletion capsules free their slot instead of blocking a seal.
- **#2** Lockfile release is token-checked (a stale holder can never
  delete a fresh holder's lock); `PermissionError` on acquire handled;
  the acquire loop respects its deadline instead of spinning; lockfile
  mode 0o600.
- **#3/#7/#10** Atomic write: fsync before rename, 0o600 tmp file,
  tmp cleaned up on any failure, `os.replace` retried on Windows
  (destination held open); store dir mkdir 0o700 (new dirs only).
- **#4** Store load: read failures and corrupt JSON get distinct honest
  errors; `capsules[]` rows are shape- and scalar-type-validated —
  wrong-shape stores refuse cleanly instead of error-looping.
- **#5** Six new tests pinning the fixes above.
- **#8** Integer arguments strict everywhere: bool and non-integral
  floats rejected (`True`, `3.9`, `nan`, `inf`), `3.0`/`"3"` coerce.
- **#9** Date parsing: fractional seconds normalized so the documented
  formats work on Python 3.9–3.10 too; `OverflowError` handled;
  `CapsuleError` hoisted above first use.
- **#11** Structured refusals: `open()` errors carry `status`,
  `eligible_at`, `remaining_seconds` (additive `details` dict) through
  the engine, the tools, and the dashboard API.
- **#12** Deletion announcements record `pending_deletion_at`,
  `was_opened`, `condition_type`.
- **D5 (warn-and-record only)** Clock-rollback detection: a high-water
  mark is persisted with each write; a backward jump > 5 minutes is
  logged one entry per episode and surfaced by `timecapsule_status`.
  Never refuses, never bricks the store in 0.1.x — the hard guard and
  escape hatch are a 0.2.0 item.
- **D1(c)** `reseal` note made honest for date capsules past their date
  (they re-enter grace on the next observation).
- **Docs** README/SECURITY: grace starts at first observation, the
  silence clock is store-wide, `author` is self-asserted (not
  authentication), the system clock is trusted (rollback recorded, not
  fatal), delete is not a secure wipe, labels are not secret, any
  mutating call is activity, anyone can start a deletion.
- **schemas.py** bounds sourced from the engine constants; the open
  tool warns that a refused open resets the silence clock.
- **CI** `.github/workflows/test.yml`: unittest on Python 3.9–3.13,
  zero pip dependencies.

## 0.1.0 — 2026-11-11

Initial release: seven tools (seal/list/open/delete/restore/reseal/
status), date/silence/manual release conditions, grace ladder, slow
announced deletion, dashboard tab, bundled skill. Validated clean with
`hermes plugins validate`.
