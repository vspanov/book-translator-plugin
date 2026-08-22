from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "book-translator" / "scripts"))
from documents import (
    add_annotations, annotations_only_change, extract_annotations, extract_rtf,
    inspect_rtf, rebuild_rtf, rtf_fingerprints, strip_annotations,
    validate_translation,
)


def fixture() -> str:
    return (
        r"{\rtf1\ansi\ansicpg1252\deff0"
        r"{\fonttbl{\f0 Times New Roman;}}"
        r"{\stylesheet{\s1\outlinelevel0 Heading;}}"
        r"\uc1\pard\s1\outlinelevel0 \u1057?\u1077?\u1074?\u1077?\u1088?\par"
        r"\pard Plain {\b bold} and {\i italic}."
        r"{\footnote\pard Footnote text.\par}"
        r"{\pict\pngblip 89504e470d0a}\par}"
    )


class RtfTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "scene.rtf"
        self.source.write_text(fixture(), encoding="latin-1")

    def tearDown(self):
        self.temporary.cleanup()

    def test_unicode_formatting_footnote_and_heading(self):
        data = extract_rtf(self.source)
        self.assertEqual("Север", data["блоки"][0]["текст"])
        self.assertEqual("заголовок", data["блоки"][0]["тип"])
        self.assertTrue(any(block["тип"] == "сноска" for block in data["блоки"]))
        body = next(block for block in data["блоки"] if "Plain" in block["текст"])
        self.assertTrue(any(fragment["полужирный"] for fragment in body["фрагменты"]))
        self.assertTrue(any(fragment["курсив"] for fragment in body["фрагменты"]))

    def test_plain_text_uses_declared_codepage(self):
        encoded = "Тихая гавань".encode("cp1251").decode("latin-1")
        path = self.directory / "codepage.rtf"
        path.write_text(r"{\rtf1\ansi\ansicpg1251 " + encoded + r"\par}", encoding="latin-1")
        self.assertEqual("Тихая гавань", extract_rtf(path)["блоки"][0]["текст"])

    def test_rebuild_preserves_image_and_formats_translation(self):
        data = extract_rtf(self.source)
        translated = copy.deepcopy(data)
        for block in translated["блоки"]:
            for fragment in block["фрагменты"]:
                fragment["текст"] = ""
            block["фрагменты"][0]["текст"] = "Новый "
            bold = next((fragment for fragment in block["фрагменты"] if fragment["полужирный"]), None)
            (bold or block["фрагменты"][0])["текст"] += "текст"
            block["текст"] = "Новый текст"
        target = self.directory / "result.rtf"
        rebuild_rtf(self.source, translated, target)
        self.assertIn(r"{\pict\pngblip 89504e470d0a}", target.read_text(encoding="latin-1"))
        rebuilt = extract_rtf(target)
        self.assertTrue(all(block["текст"] == "Новый текст" for block in rebuilt["блоки"]))
        body = next(block for block in rebuilt["блоки"] if block["текст"] == "Новый текст" and any(fragment["полужирный"] for fragment in block["фрагменты"]))
        self.assertTrue(any(fragment["полужирный"] for fragment in body["фрагменты"]))

    def test_binary_image_payload_is_preserved(self):
        payload = "{\\}\x00"
        path = self.directory / "binary.rtf"
        path.write_text(r"{\rtf1 Text{\pict\bin4 " + payload + r"}\par}", encoding="latin-1")
        data = extract_rtf(path)
        data["блоки"][0]["фрагменты"][0]["текст"] = "Текст"
        target = self.directory / "binary-result.rtf"
        rebuild_rtf(path, data, target)
        self.assertIn(payload, target.read_text(encoding="latin-1"))

    def test_unknown_ignorable_destination_is_not_translated(self):
        path = self.directory / "service.rtf"
        path.write_text(r"{\rtf1 Visible{\*\vendor private text}\par}", encoding="latin-1")
        self.assertEqual("Visible", extract_rtf(path)["блоки"][0]["текст"])

    def test_native_annotation_round_trip_and_removal(self):
        before = rtf_fingerprints(self.source)
        add_annotations(self.source, [{
            "id": "issue-001", "точная_цитата": "Plain", "номер_вхождения": 1,
            "серьезность": "смысловая", "объяснение": "Уточнить оттенок.",
            "минимальная_рекомендация": "Сверить сцену.",
        }])
        annotations = extract_annotations(self.source)
        self.assertEqual("issue-001", annotations[0]["id"])
        self.assertEqual("Plain", annotations[0]["цитата"])
        after = rtf_fingerprints(self.source)
        self.assertTrue(annotations_only_change(before, after))
        strip_annotations(self.source)
        self.assertEqual([], extract_annotations(self.source))
        self.assertEqual(before["текст"], rtf_fingerprints(self.source)["текст"])

    def test_rejects_unsafe_text_destination_and_broken_rtf(self):
        unsafe = self.directory / "unsafe.rtf"
        unsafe.write_text(r"{\rtf1{\field hidden}}", encoding="latin-1")
        self.assertTrue(inspect_rtf(unsafe))
        broken = self.directory / "broken.rtf"
        broken.write_text(r"{\rtf1 broken", encoding="latin-1")
        self.assertTrue(inspect_rtf(broken))

    def test_forbidden_letter_is_mechanical_error(self):
        errors = validate_translation([{"текст": "birch"}], [{"текст": "берёза"}])
        self.assertEqual(1, len(errors))


if __name__ == "__main__":
    unittest.main()
