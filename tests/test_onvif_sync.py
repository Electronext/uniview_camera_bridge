from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SharedONVIFSyncTests(unittest.TestCase):
    def test_addon_runtime_copies_match_canonical_source(self):
        canonical = (ROOT / "shared" / "onvif_camera.py").read_bytes()
        for relative in (
            "uniview_camera_bridge/onvif_camera.py",
            "tapo_camera_bridge/onvif_camera.py",
        ):
            self.assertEqual(canonical, (ROOT / relative).read_bytes(), relative)


if __name__ == "__main__":
    unittest.main()
