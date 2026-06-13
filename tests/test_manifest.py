import json
import tempfile
import unittest
from pathlib import Path

from prompt_performance_engine.manifest import build_manifest, verify_manifest


class ManifestTests(unittest.TestCase):
    def test_build_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.txt"
            second = root / "b.json"
            first.write_text("alpha", encoding="utf-8")
            second.write_text('{"value": 2}', encoding="utf-8")
            manifest = build_manifest([second, first], root=root)
            self.assertEqual(verify_manifest(manifest, root=root), [])
            self.assertEqual(
                [entry["path"] for entry in manifest["entries"]],
                ["a.txt", "b.json"],
            )

    def test_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "artifact.json"
            path.write_text(json.dumps({"ok": True}), encoding="utf-8")
            manifest = build_manifest([path], root=root)
            path.write_text(json.dumps({"ok": False}), encoding="utf-8")
            self.assertTrue(verify_manifest(manifest, root=root))


if __name__ == "__main__":
    unittest.main()
