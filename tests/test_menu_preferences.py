"""Preference validation tests; no GUI, Windows APIs, network or real profile."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from menu_ui import CompanionCard, SettingsSkin, LOOKS


class PreferencesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'preferences.json'

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')

    def test_invalid_and_unknown_look_falls_back(self):
        for data in [[], None, {'look': 'unknown'}, {'look': 3}, {'look': []}, {'look': {}} ]:
            with self.subTest(data=data):
                self.write(data)
                self.assertEqual(SettingsSkin(SimpleNamespace(), self.path).mode, 'pink')
        self.path.write_text('{broken', encoding='utf-8')
        self.assertEqual(SettingsSkin(SimpleNamespace(), self.path).mode, 'pink')

    def test_all_looks_roundtrip_without_core_config(self):
        for mode in LOOKS:
            skin = SettingsSkin(SimpleNamespace(), self.path)
            skin.set_palette(mode)
            skin.save()
            loaded = SettingsSkin(SimpleNamespace(), self.path)
            self.assertEqual(loaded.mode, mode)
            self.assertEqual(loaded.c, skin.c)
            self.assertEqual(set(json.loads(self.path.read_text())), {'look'})

    def test_companion_validates_types_and_unknown_characters(self):
        dummy = SimpleNamespace(path=self.path, NAMES=CompanionCard.NAMES)
        for data in [[], None, {'character': []}, {'character': 'bad', 'automatic': 'false', 'motion': 'true'}]:
            self.write(data)
            settings = CompanionCard._read(dummy)
            self.assertEqual(settings['character'], 'boykisser')
            self.assertFalse(settings['automatic'])
        self.write({'character': 'mimi', 'automatic': True, 'motion': False})
        self.assertEqual(CompanionCard._read(dummy),
                         {'character': 'mimi', 'automatic': True, 'motion': False})


if __name__ == '__main__':
    unittest.main()
