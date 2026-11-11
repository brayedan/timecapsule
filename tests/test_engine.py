"""Engine tests for timecapsule — stdlib only, run with:

    python -m unittest discover -s tests -v
"""

import json
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from timecapsule_engine import CapsuleError, TimeCapsule

T0 = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    """Controllable clock: tests advance time instead of waiting days."""

    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


def _fresh():
    root = pathlib.Path(tempfile.mkdtemp())
    clock = FakeClock()
    return root, clock, TimeCapsule(root, now_fn=clock)


class TestSealAndList(unittest.TestCase):
    def test_seal_returns_metadata_only(self):
        _, _, tc = _fresh()
        r = tc.seal("the launch codes are 1234", "date",
                    release_date="2026-11-11T15:00:00Z", author="hyperion",
                    label="launch")
        self.assertEqual(r["id"], "cap-0001")
        self.assertEqual(r["status"], "sealed")
        self.assertNotIn("message", r)
        self.assertNotIn("content", r)

    def test_list_never_shows_content(self):
        _, _, tc = _fresh()
        tc.seal("secret plans", "manual", author="hyperion")
        tc.seal("more secrets", "date", release_date="2027-01-01T00:00:00Z")
        out = tc.list_capsules()
        blob = json.dumps(out)
        self.assertNotIn("secret plans", blob)
        self.assertNotIn("more secrets", blob)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["capsules"][0]["condition"]["type"], "manual")

    def test_list_status_filter(self):
        _, _, tc = _fresh()
        tc.seal("a", "manual", author="hyperion")
        tc.seal("b", "date", release_date="2027-01-01T00:00:00Z")
        tc.delete_capsule("cap-0002")
        sealed = tc.list_capsules("sealed")
        self.assertEqual(sealed["count"], 1)
        self.assertEqual(sealed["capsules"][0]["id"], "cap-0001")
        with self.assertRaises(CapsuleError):
            tc.list_capsules("bogus")


class TestDateCondition(unittest.TestCase):
    def test_open_before_date_refuses(self):
        _, _, tc = _fresh()
        tc.seal("future note", "date", release_date="2026-11-11T15:00:00Z")
        with self.assertRaises(CapsuleError) as cm:
            tc.open_capsule("cap-0001")
        self.assertIn("2026-11-11T15:00:00", str(cm.exception))

    def test_open_after_date_zero_grace(self):
        _, _, tc = _fresh()
        tc.seal("past note", "date", release_date="2026-11-01T00:00:00Z",
                 grace_days=0)
        r = tc.open_capsule("cap-0001")
        self.assertTrue(r["ok"])
        self.assertEqual(r["content"], "past note")

    def test_grace_ladder(self):
        _, clock, tc = _fresh()
        tc.seal("grace note", "date", release_date="2026-11-01T00:00:00Z",
                 grace_days=3, author="hyperion")
        # Condition met, but grace is running: open refuses, status is grace.
        with self.assertRaises(CapsuleError) as cm:
            tc.open_capsule("cap-0001")
        self.assertIn("grace until", str(cm.exception))
        self.assertEqual(tc.list_capsules()["capsules"][0]["status"], "grace")
        # Wrong sealer cannot re-seal.
        with self.assertRaises(CapsuleError):
            tc.reseal_capsule("cap-0001", author="decomp")
        # Right sealer re-seals: back to sealed.
        r = tc.reseal_capsule("cap-0001", author="hyperion")
        self.assertEqual(r["status"], "sealed")
        # Condition still met -> grace again; wait it out -> open.
        clock.advance(days=0)  # next refresh re-enters grace
        tc.list_capsules()
        self.assertEqual(tc.list_capsules()["capsules"][0]["status"], "grace")
        clock.advance(days=3)
        r = tc.open_capsule("cap-0001")
        self.assertEqual(r["content"], "grace note")


class TestSilenceCondition(unittest.TestCase):
    def test_silence_opens_after_quiet(self):
        _, clock, tc = _fresh()
        tc.seal("if you read this, I went quiet", "silence", silence_days=2,
                 grace_days=0)
        # 1 day of silence is not enough.
        clock.advance(days=1)
        with self.assertRaises(CapsuleError) as cm:
            tc.open_capsule("cap-0001")
        self.assertIn("silence", str(cm.exception))
        # Note: that refused open attempt itself counts as activity, so the
        # clock restarts from it. 2 more quiet days, then it opens.
        clock.advance(days=2)
        r = tc.open_capsule("cap-0001")
        self.assertEqual(r["content"], "if you read this, I went quiet")

    def test_activity_resets_silence_clock(self):
        _, clock, tc = _fresh()
        tc.seal("quiet note", "silence", silence_days=2, grace_days=0)
        clock.advance(days=1)
        tc.seal("another capsule", "manual", author="hyperion")  # activity
        clock.advance(days=1)
        # Only 1 day of silence since the last activity.
        with self.assertRaises(CapsuleError):
            tc.open_capsule("cap-0001")
        # That refused open was itself activity, so the clock restarted
        # again: 2 more quiet days are needed from the refusal.
        clock.advance(days=2)
        r = tc.open_capsule("cap-0001")
        self.assertEqual(r["content"], "quiet note")


class TestManualCondition(unittest.TestCase):
    def test_manual_only_sealer_releases(self):
        _, _, tc = _fresh()
        tc.seal("hand-delivered", "manual", author="hyperion", grace_days=0)
        with self.assertRaises(CapsuleError) as cm:
            tc.open_capsule("cap-0001", author="decomp")
        self.assertIn("only the sealer", str(cm.exception))
        r = tc.open_capsule("cap-0001", author="hyperion")
        self.assertEqual(r["content"], "hand-delivered")

    def test_manual_grace_applies(self):
        _, clock, tc = _fresh()
        tc.seal("hand-delivered", "manual", author="hyperion", grace_days=2)
        with self.assertRaises(CapsuleError) as cm:
            tc.open_capsule("cap-0001", author="hyperion")
        self.assertIn("grace until", str(cm.exception))
        clock.advance(days=2)
        r = tc.open_capsule("cap-0001", author="hyperion")
        self.assertEqual(r["content"], "hand-delivered")


class TestDeleteRestore(unittest.TestCase):
    def test_delete_pending_restore(self):
        _, clock, tc = _fresh()
        tc.seal("doomed note", "date", release_date="2027-01-01T00:00:00Z",
                 delete_days=7)
        r = tc.delete_capsule("cap-0001")
        self.assertEqual(r["status"], "pending-deletion")
        self.assertIn("7 days", r["note"])
        # Pending deletion cannot be opened.
        with self.assertRaises(CapsuleError) as cm:
            tc.open_capsule("cap-0001")
        self.assertIn("pending deletion", str(cm.exception))
        # Restore brings it back, condition intact.
        r = tc.restore_capsule("cap-0001")
        self.assertEqual(r["status"], "sealed")
        with self.assertRaises(CapsuleError):
            tc.open_capsule("cap-0001")  # date still in the future

    def test_delete_countdown_expiry_announces(self):
        root, clock, tc = _fresh()
        tc.seal("short-lived", "manual", author="hyperion", delete_days=1)
        tc.delete_capsule("cap-0001")
        clock.advance(days=1)
        st = tc.status()
        self.assertEqual(st["total_capsules"], 0)
        self.assertEqual(st["deletions_announced"], 1)
        self.assertEqual(st["deletions"][0]["id"], "cap-0001")
        self.assertEqual(st["deletions"][0]["reason"], "deletion countdown expired")
        # The announcement is permanent: the store still holds it.
        store = json.loads((root / "store.json").read_text())
        self.assertEqual(len(store["deletions"]), 1)
        self.assertEqual(store["capsules"], [])
        # And the id is never reused.
        tc.seal("next", "manual", author="hyperion")
        self.assertEqual(tc.list_capsules()["capsules"][0]["id"], "cap-0002")

    def test_restore_from_wrong_status_refused(self):
        _, _, tc = _fresh()
        tc.seal("safe", "manual", author="hyperion")
        with self.assertRaises(CapsuleError):
            tc.restore_capsule("cap-0001")
        with self.assertRaises(CapsuleError):
            tc.reseal_capsule("cap-0001", author="hyperion")


class TestBoundsAndValidation(unittest.TestCase):
    def test_message_bounds(self):
        _, _, tc = _fresh()
        with self.assertRaises(CapsuleError):
            tc.seal("", "manual", author="hyperion")
        with self.assertRaises(CapsuleError) as cm:
            tc.seal("x" * 20001, "manual", author="hyperion")
        self.assertIn("exceeds 20000", str(cm.exception))

    def test_label_bound(self):
        _, _, tc = _fresh()
        with self.assertRaises(CapsuleError):
            tc.seal("ok", "manual", author="hyperion", label="x" * 101)

    def test_condition_params_validated(self):
        _, _, tc = _fresh()
        with self.assertRaises(CapsuleError):
            tc.seal("ok", "date")  # no release_date
        with self.assertRaises(CapsuleError):
            tc.seal("ok", "date", release_date="not a date")
        with self.assertRaises(CapsuleError):
            tc.seal("ok", "silence")  # no silence_days
        with self.assertRaises(CapsuleError):
            tc.seal("ok", "silence", silence_days=0)
        with self.assertRaises(CapsuleError):
            tc.seal("ok", "silence", silence_days=366)
        with self.assertRaises(CapsuleError):
            tc.seal("ok", "manual", grace_days=31)
        with self.assertRaises(CapsuleError):
            tc.seal("ok", "bogus-condition")

    def test_id_validation(self):
        _, _, tc = _fresh()
        tc.seal("ok", "manual", author="hyperion")
        for bad in ("../../../etc", "cap-1", "cap-abc", "", None):
            with self.assertRaises(CapsuleError) as cm:
                tc.open_capsule(bad)
            self.assertIn("invalid capsule id", str(cm.exception))
        with self.assertRaises(CapsuleError) as cm:
            tc.open_capsule("cap-9999")
        self.assertIn("no such capsule", str(cm.exception))

    def test_max_capsules(self):
        root, _, tc = _fresh()
        store = {"version": 1, "next_id": 101, "last_activity_at": None,
                 "capsules": [{"id": f"cap-{i:04d}", "label": "", "sealed_by": "",
                               "sealed_at": "2026-11-08T12:00:00+00:00", "message": "x",
                               "condition": {"type": "manual"}, "grace_days": 3,
                               "delete_days": 7, "status": "sealed",
                               "grace_started_at": None, "grace_ends_at": None,
                               "pending_deletion_at": None, "delete_after": None,
                               "opened_at": None} for i in range(1, 101)],
                 "deletions": []}
        (root / "store.json").write_text(json.dumps(store))
        with self.assertRaises(CapsuleError) as cm:
            tc.seal("one too many", "manual", author="hyperion")
        self.assertIn("maximum of 100", str(cm.exception))

    def test_corrupt_store_refuses(self):
        root, _, tc = _fresh()
        (root / "store.json").write_text("{not json")
        with self.assertRaises(CapsuleError) as cm:
            tc.seal("ok", "manual", author="hyperion")
        self.assertIn("corrupt", str(cm.exception))


class TestStatus(unittest.TestCase):
    def test_status_counts(self):
        _, _, tc = _fresh()
        tc.seal("a", "manual", author="hyperion")
        tc.seal("b", "date", release_date="2027-01-01T00:00:00Z")
        tc.delete_capsule("cap-0002")
        st = tc.status()
        self.assertEqual(st["counts"]["sealed"], 1)
        self.assertEqual(st["counts"]["pending-deletion"], 1)
        self.assertEqual(st["counts"]["grace"], 0)
        self.assertEqual(st["counts"]["open"], 0)
        self.assertEqual(st["total_capsules"], 2)
        self.assertEqual(st["deletions_announced"], 0)


if __name__ == "__main__":
    unittest.main()
