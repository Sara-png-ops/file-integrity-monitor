"""Tests for the core FIM logic in main.py.

Run from the project folder with:  python -m unittest -v
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main as fim


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class FimTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.folder = self.tmp / "monitored"
        self.folder.mkdir()
        # Keep baselines inside the temp dir, not the real project folder.
        patcher = mock.patch.object(fim, "BASELINE_DIR", self.tmp / "baselines")
        patcher.start()
        self.addCleanup(patcher.stop)

    def snapshot_and_compare(self, baseline_state=None):
        """Compare a stored scan against a fresh scan of the folder."""
        original, baseline_unreadable = baseline_state
        current, unreadable = fim.scan_folder(self.folder)
        results, counts = fim.compare_snapshots(
            original, current, unreadable, baseline_unreadable
        )
        return {name: status for name, status, _ in results}, counts


class TestHashing(FimTestCase):
    def test_known_sha256(self):
        f = self.folder / "a.txt"
        f.write_bytes(b"abc")
        self.assertEqual(
            fim.calculate_hash(f),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_empty_file(self):
        f = self.folder / "empty.txt"
        f.write_bytes(b"")
        self.assertEqual(
            fim.calculate_hash(f),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )


class TestDetection(FimTestCase):
    def test_modified_deleted_new_unchanged(self):
        write(self.folder / "same.txt", "same")
        write(self.folder / "edit.txt", "before")
        write(self.folder / "gone.txt", "bye")
        base = fim.scan_folder(self.folder)

        write(self.folder / "edit.txt", "after")
        (self.folder / "gone.txt").unlink()
        write(self.folder / "sub" / "new.txt", "hello")

        statuses, counts = self.snapshot_and_compare(base)
        self.assertEqual(statuses["same.txt"], "UNCHANGED")
        self.assertEqual(statuses["edit.txt"], "MODIFIED")
        self.assertEqual(statuses["gone.txt"], "DELETED")
        self.assertEqual(statuses[os.path.join("sub", "new.txt")], "NEW")
        self.assertEqual(counts["UNREADABLE"], 0)

    def test_symlinks_are_ignored(self):
        write(self.folder / "real.txt", "x")
        try:
            os.symlink(self.folder / "real.txt", self.folder / "link.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported here")
        hashes, unreadable = fim.scan_folder(self.folder)
        self.assertEqual(list(hashes), ["real.txt"])
        self.assertEqual(unreadable, [])


class TestUnreadable(FimTestCase):
    def _make_unreadable(self, name):
        real_hash = fim.calculate_hash

        def fake(path):
            if Path(path).name == name:
                raise PermissionError("denied")
            return real_hash(path)

        return mock.patch.object(fim, "calculate_hash", side_effect=fake)

    def test_file_becoming_unreadable_is_not_deleted(self):
        write(self.folder / "secret.txt", "data")
        write(self.folder / "ok.txt", "data")
        base = fim.scan_folder(self.folder)

        with self._make_unreadable("secret.txt"):
            statuses, counts = self.snapshot_and_compare(base)

        self.assertEqual(statuses["secret.txt"], "UNREADABLE")
        self.assertEqual(statuses["ok.txt"], "UNCHANGED")
        self.assertEqual(counts["UNREADABLE"], 1)
        self.assertEqual(counts["DELETED"], 0)

    def test_new_unreadable_file_is_not_new(self):
        write(self.folder / "ok.txt", "data")
        base = fim.scan_folder(self.folder)
        write(self.folder / "added.txt", "data")

        with self._make_unreadable("added.txt"):
            statuses, counts = self.snapshot_and_compare(base)

        self.assertEqual(statuses["added.txt"], "UNREADABLE")
        self.assertEqual(counts["NEW"], 0)

    def test_file_unreadable_at_baseline_then_readable_is_new(self):
        write(self.folder / "late.txt", "data")
        write(self.folder / "ok.txt", "data")
        with self._make_unreadable("late.txt"):
            base = fim.scan_folder(self.folder)
        self.assertEqual(base[1], ["late.txt"])

        statuses, _ = self.snapshot_and_compare(base)
        self.assertEqual(statuses["late.txt"], "NEW")

    def test_unreadable_directory_files_are_not_deleted(self):
        write(self.folder / "locked" / "a.txt", "data")
        write(self.folder / "ok.txt", "data")
        base = fim.scan_folder(self.folder)

        locked = str(self.folder / "locked")
        real_walk = os.walk

        def fake_walk(top, onerror=None, **kwargs):
            for dirpath, dirs, files in real_walk(top, onerror=onerror, **kwargs):
                if "locked" in dirs:
                    dirs.remove("locked")
                    onerror(PermissionError(13, "denied", locked))
                yield dirpath, dirs, files

        with mock.patch.object(fim.os, "walk", fake_walk):
            statuses, counts = self.snapshot_and_compare(base)

        self.assertEqual(statuses[os.path.join("locked", "a.txt")], "UNREADABLE")
        self.assertEqual(counts["DELETED"], 0)


class TestScanExclusions(FimTestCase):
    def test_baseline_directory_is_excluded(self):
        write(self.folder / "a.txt", "a")
        # Monitor a folder that contains the baseline directory.
        monitored = self.tmp
        hashes, _ = fim.scan_folder(monitored)
        self.assertIn(os.path.join("monitored", "a.txt"), hashes)

        (fim.BASELINE_DIR).mkdir(exist_ok=True)
        write(fim.BASELINE_DIR / "x.json", "{}")
        hashes, _ = fim.scan_folder(monitored)
        self.assertFalse(any("baselines" in name for name in hashes))


class TestBaselineStorage(FimTestCase):
    def test_round_trip_and_atomic_write(self):
        write(self.folder / "a.txt", "a")
        hashes, unreadable = fim.scan_folder(self.folder)
        path = fim.get_baseline_path(self.folder)
        fim.save_baseline(path, fim.build_baseline(self.folder, hashes, unreadable))

        self.assertTrue(path.exists())
        self.assertFalse(path.with_name(path.name + ".tmp").exists())
        files, unread = fim.load_baseline(path, self.folder)
        self.assertEqual(files, hashes)
        self.assertEqual(unread, [])

    def test_failed_write_keeps_old_baseline(self):
        path = fim.get_baseline_path(self.folder)
        fim.save_baseline(path, {"ok": True})
        with mock.patch.object(fim.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                fim.save_baseline(path, {"ok": False})
        self.assertEqual(json.loads(path.read_text()), {"ok": True})
        self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_validation_rejects_bad_baselines(self):
        good = fim.build_baseline(self.folder, {"a.txt": "0" * 64}, [])
        fim.validate_baseline(good, self.folder)  # should not raise

        bad_cases = {
            "not a dict": [],
            "wrong folder": {**good, "folder": "/somewhere/else"},
            "files not a dict": {**good, "files": ["a.txt"]},
            "files missing": {"folder": good["folder"]},
            "bad hash": {**good, "files": {"a.txt": "nothash"}},
            "bad unreadable": {**good, "unreadable": "oops"},
        }
        for label, data in bad_cases.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    fim.validate_baseline(data, self.folder)

    def test_old_baseline_without_unreadable_field_is_accepted(self):
        old = fim.build_baseline(self.folder, {"a.txt": "0" * 64}, [])
        del old["unreadable"]
        files, unread = fim.validate_baseline(old, self.folder)
        self.assertEqual(unread, [])


if __name__ == "__main__":
    unittest.main()
