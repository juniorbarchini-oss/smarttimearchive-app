import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sta import discover  # noqa: E402
from sta.apfs import ApfsError  # noqa: E402


def vol(name, dev, roles=("Backup",), used=1000, locked=False):
    return {
        "Name": name,
        "DeviceIdentifier": dev,
        "Roles": list(roles),
        "CapacityInUse": used,
        "Locked": locked,
    }


APFS_LIST = {
    "Containers": [
        {
            "Volumes": [
                vol("Macintosh HD", "disk3s1", roles=("System",)),
                vol("Good USB", "disk7s2", used=281_700_000_000),
                vol("Locked USB", "disk8s1", locked=True),
                vol("Unmounted", "disk9s1"),
                vol("Net image", "disk5s1"),
                vol("Empty backup", "disk10s1"),
            ]
        }
    ]
}
INFO = {
    "disk7s2": {"MountPoint": "/Volumes/Good USB", "BusProtocol": "USB"},
    "disk8s1": {"MountPoint": "", "BusProtocol": "USB"},
    "disk9s1": {"MountPoint": "", "BusProtocol": "USB"},
    "disk5s1": {"MountPoint": "/Volumes/Net", "BusProtocol": "Disk Image"},
    "disk10s1": {"MountPoint": "/Volumes/Empty", "BusProtocol": "USB"},
}


def fake_snaps(mp):
    if mp == "/Volumes/Good USB":
        return [("2026-09-15-115449", "n", "u"), ("2026-09-18-194541", "n", "u")]
    if mp == "/Volumes/Empty":
        return []
    raise ApfsError("boom")


class TestBackupVolumes(unittest.TestCase):
    def setUp(self):
        vols = discover.find_backup_volumes(APFS_LIST, info=lambda d: INFO[d], snapshots=fake_snaps)
        self.by = {v.name: v for v in vols}

    def test_only_backup_role_volumes_are_listed(self):
        self.assertNotIn("Macintosh HD", self.by)
        self.assertEqual(len(self.by), 5)

    def test_local_usb_backup_is_the_only_supported_source(self):
        good = self.by["Good USB"]
        self.assertTrue(good.supported)
        self.assertEqual(good.snapshots, ["2026-09-15-115449", "2026-09-18-194541"])
        self.assertEqual(good.used_bytes, 281_700_000_000)
        self.assertEqual([n for n, v in self.by.items() if v.supported], ["Good USB"])

    def test_every_unusable_volume_says_why(self):
        self.assertIn("locked", self.by["Locked USB"].note)
        self.assertIn("not mounted", self.by["Unmounted"].note)
        self.assertIn("network image", self.by["Net image"].note)
        self.assertIn("no Time Machine snapshots", self.by["Empty backup"].note)
        for name in ("Locked USB", "Unmounted", "Net image", "Empty backup"):
            self.assertFalse(self.by[name].supported)

    def test_no_backups_at_all(self):
        self.assertEqual(discover.find_backup_volumes({"Containers": []}), [])


class TestDestinations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sta_disc_")
        for n in ("Fast_APFS", "Stick_exFAT", "Share", "Recovery", ".hidden"):
            os.makedirs(os.path.join(self.tmp, n))
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.mounts = {
            os.path.join(self.tmp, "Fast_APFS"): "apfs",
            os.path.join(self.tmp, "Stick_exFAT"): "exfat",
            os.path.join(self.tmp, "Share"): "smbfs",
            os.path.join(self.tmp, "Recovery"): "apfs",
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def find(self, **kw):
        return {
            d.name: d
            for d in discover.find_destinations(
                volumes_dir=self.tmp,
                home=self.home,
                mounts=self.mounts,
                is_mount=lambda p: p in self.mounts,
                internal=lambda p: p.endswith("Recovery"),
                **kw,
            )
        }

    def test_capabilities_by_filesystem(self):
        d = self.find()
        self.assertTrue(d["Fast_APFS"].hardlinks)
        self.assertFalse(d["Stick_exFAT"].hardlinks)
        self.assertFalse(d["Share"].hardlinks)
        self.assertTrue(d["Share"].network)
        self.assertFalse(d["Stick_exFAT"].network)

    def test_home_is_offered_and_system_volumes_and_hidden_are_not(self):
        d = self.find()
        self.assertIn("This Mac (home folder)", d)
        self.assertNotIn("Recovery", d)
        self.assertNotIn(".hidden", d)

    def test_source_disk_is_flagged(self):
        src = discover.BackupVolume("s", "d", mountpoint=os.path.join(self.tmp, "Fast_APFS"))
        d = self.find(sources=[src])
        self.assertTrue(d["Fast_APFS"].is_backup_source)
        self.assertFalse(d["Stick_exFAT"].is_backup_source)

    def test_existing_archive_is_recognised(self):
        stick = os.path.join(self.tmp, "Stick_exFAT")
        os.makedirs(os.path.join(stick, "_sta", "manifests"))
        for date in ("2026-09-12-123902", "2026-09-15-115449"):
            open(os.path.join(stick, "_sta", "manifests", date + ".sha256"), "w").close()
        os.makedirs(os.path.join(stick, "SmartTimeArchive.sparsebundle"))
        d = self.find()
        self.assertEqual(d["Stick_exFAT"].archive_dates, ["2026-09-12-123902", "2026-09-15-115449"])
        self.assertTrue(d["Stick_exFAT"].has_image)
        self.assertEqual(d["Fast_APFS"].archive_dates, [])

    def test_legacy_backup_detection_ignores_system_folders(self):
        for n in ("Old_TM", "com.apple.TimeMachine.localsnapshots"):
            os.makedirs(os.path.join(self.tmp, n, "Backups.backupdb"))
            self.mounts[os.path.join(self.tmp, n)] = "hfs"
        found = discover.find_legacy_backups(self.tmp, is_mount=lambda p: p in self.mounts)
        self.assertEqual(found, ["Old_TM"])


if __name__ == "__main__":
    unittest.main()
