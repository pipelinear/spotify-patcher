"""Regression checks for the binary/metadata transformations in the Chroma kit path."""
import struct
import unittest
from chroma import inject, load_commands, merge_intents

class InjectionTests(unittest.TestCase):
    def binary(self, section=256):
        data = bytearray(512)
        struct.pack_into('<IIIIIIII', data, 0, 0xfeedfacf, 0x100000c, 0, 2, 1, 152, 0, 0)
        struct.pack_into('<II', data, 32, 0x19, 152)
        struct.pack_into('<I', data, 32 + 64, 1)
        struct.pack_into('<I', data, 32 + 72 + 48, section)
        data[section:] = b'X' * (512 - section)
        return data

    def test_injection_preserves_sections_and_is_idempotent(self):
        original = self.binary(384)
        patched = inject(original, ['@rpath/mod.dylib'], '@executable_path/Frameworks')
        self.assertEqual(original[384:], patched[384:])
        self.assertEqual(original[32:184], patched[32:184])
        self.assertEqual([cmd for _, cmd, _ in load_commands(patched)], [0x19, 0x8000001c, 0x80000018])
        self.assertEqual(patched, inject(patched, ['@rpath/mod.dylib'], '@executable_path/Frameworks'))

    def test_rejects_overflow_without_changing_input(self):
        original = self.binary(192)
        before = bytes(original)
        with self.assertRaisesRegex(ValueError, 'no space'):
            inject(original, ['@rpath/mod.dylib'])
        self.assertEqual(bytes(original), before)

    def test_rejects_nonzero_header_padding(self):
        original = self.binary(384)
        original[185] = 1
        with self.assertRaisesRegex(ValueError, 'no space'):
            inject(original, ['@rpath/mod.dylib'])

    def test_intents_preserve_spotify_and_replace_duplicate_ids(self):
        original = dict(actions={'spotify': 1}, enums=[{'identifier':'same','value':'old'},{'identifier':'spotify'}], negativePhrases=['one'], unrelated='kept')
        added = dict(actions={'mod': 2}, enums=[{'identifier':'same','value':'new'}], negativePhrases=['one','two'])
        merged = merge_intents(original, added)
        self.assertEqual(merged['actions'], {'spotify':1,'mod':2})
        self.assertEqual(merged['enums'], [{'identifier':'spotify'},{'identifier':'same','value':'new'}])
        self.assertEqual(merged['negativePhrases'], ['one','two'])
        self.assertEqual(merged['unrelated'], 'kept')
        self.assertEqual(original['enums'][0]['value'], 'old')

if __name__ == '__main__':
    unittest.main()
