"""Validate shipped translations and stable entity translation keys without HA imports."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "pylon_fh3x"


class TranslationTests(unittest.TestCase):
    def test_translation_coverage_and_entity_keys(self):
        translations = {
            lang: json.loads((ROOT / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
            for lang in ("en", "nl")
        }
        self.assertEqual(set(translations["en"]["entity"]), {"sensor", "number", "select", "switch"})
        for domain in ("sensor", "number", "select", "switch"):
            source = (ROOT / f"{domain}.py").read_text(encoding="utf-8")
            pairs = re.findall(r'key="([^"]+)",\s*translation_key="([^"]+)",\s*name=', source)
            self.assertTrue(pairs)
            self.assertTrue(all(key == translation_key for key, translation_key in pairs))
            keys = {key for key, _ in pairs}
            for lang in ("en", "nl"):
                with self.subTest(domain=domain, lang=lang):
                    actual = translations[lang]["entity"][domain]
                    self.assertEqual(set(actual), keys)
                    self.assertTrue(all(actual[key].get("name") for key in keys))
        for lang in ("en", "nl"):
            self.assertEqual(
                set(translations[lang]["config"]["step"]["user"]["data"]),
                {"host", "port"},
            )
        self.assertEqual(
            translations["en"],
            json.loads((ROOT / "strings.json").read_text(encoding="utf-8")),
        )


if __name__ == "__main__":
    unittest.main()
