"""Run from repo root with:
python -m unittest discover -s inverter_model_maker/model_maker_web_v3/tests -v
"""

import importlib
import unittest

from inverter_model_maker.model_maker_web_v3.backend.pipeline import stage1


class Stage1ImportSmokeTests(unittest.TestCase):
    def test_stage1_module_imports_and_json_schemas_exist(self) -> None:
        module = importlib.import_module(
            "inverter_model_maker.model_maker_web_v3.backend.pipeline.stage1"
        )

        self.assertIs(module, stage1)
        self.assertIsInstance(stage1._PASS1_JSON_SCHEMA, dict)
        self.assertIsInstance(stage1._PASS2_JSON_SCHEMA, dict)
        pass1_items = stage1._PASS1_JSON_SCHEMA["items"]
        pass2_items = stage1._PASS2_JSON_SCHEMA["items"]
        self.assertIsInstance(pass1_items, dict)
        self.assertIsInstance(pass2_items, dict)
        if not isinstance(pass1_items, dict) or not isinstance(pass2_items, dict):
            self.fail("JSON schema items must be object schemas")
        self.assertIsInstance(pass1_items["required"], list)
        self.assertIsInstance(pass2_items["required"], list)


class ModelParamBillionsTests(unittest.TestCase):
    def test_extracts_expected_sizes_and_fallback(self) -> None:
        cases = {
            "gemma4:12b": 12.0,
            "gemma4:12B": 12.0,
            "qwen3:14b": 14.0,
            "model:e2b": 2.0,
            "model:e4b": 4.0,
            "7b-instruct-q4_0": 7.0,
            "no-size-tag": 14.0,
        }

        for model_name, expected in cases.items():
            with self.subTest(model_name=model_name):
                self.assertEqual(stage1._model_param_billions(model_name), expected)


class FormatPageTablesTests(unittest.TestCase):
    def test_empty_tables_render_empty_string(self) -> None:
        self.assertEqual(stage1._format_page_tables([]), "")

    def test_tables_render_markdown_and_normalize_rows(self) -> None:
        tables = [
            [
                ["Address", "Name", "Unit"],
                [None, "Voltage", "V"],
                ["", "", ""],
                ["0x1001", "Current"],
            ]
        ]

        rendered = stage1._format_page_tables(tables)

        self.assertEqual(
            rendered,
            "\n".join(
                [
                    "[Table 1]",
                    "| Address | Name | Unit |",
                    "| --- | --- | --- |",
                    "|  | Voltage | V |",
                    "| 0x1001 | Current |  |",
                ]
            ),
        )


class ValidateAndFixScalesTests(unittest.TestCase):
    def test_expected_scale_passes_within_tolerance(self) -> None:
        expected_scale = stage1._SCALE_CONVENTION["ac_voltage"]
        registers = [{"h01_field": "ac_voltage", "scale": expected_scale}]

        result = stage1._validate_and_fix_scales(registers)

        self.assertEqual(result[0]["scale"], expected_scale)
        self.assertNotIn("_validation_note", result[0])
        self.assertNotIn("_scale_original", result[0])

    def test_decade_mismatch_is_auto_corrected(self) -> None:
        expected_scale = stage1._SCALE_CONVENTION["ac_voltage"]
        registers = [{"h01_field": "ac_voltage", "scale": expected_scale * 10}]

        result = stage1._validate_and_fix_scales(registers)

        self.assertEqual(result[0]["scale"], expected_scale)
        self.assertEqual(result[0]["_scale_original"], expected_scale * 10)
        self.assertIn("auto-corrected", result[0]["_validation_note"])

    def test_reciprocal_decade_mismatch_is_auto_corrected(self) -> None:
        expected_scale = stage1._SCALE_CONVENTION["ac_voltage"]
        registers = [{"h01_field": "ac_voltage", "scale": expected_scale / 10}]

        result = stage1._validate_and_fix_scales(registers)

        self.assertEqual(result[0]["scale"], expected_scale)
        self.assertEqual(result[0]["_scale_original"], expected_scale / 10)
        self.assertIn("auto-corrected", result[0]["_validation_note"])

    def test_non_decade_mismatch_is_flagged_without_modification(self) -> None:
        registers = [{"h01_field": "ac_voltage", "scale": 0.37}]

        result = stage1._validate_and_fix_scales(registers)

        self.assertEqual(result[0]["scale"], 0.37)
        self.assertNotIn("_scale_original", result[0])
        self.assertIn("manual review", result[0]["_validation_note"])

    def test_unknown_or_non_positive_scales_pass_through(self) -> None:
        registers = [
            {"h01_field": "inner_temp", "scale": 123},
            {"h01_field": "ac_voltage", "scale": None},
            {"h01_field": "ac_voltage", "scale": 0},
            {"h01_field": "ac_voltage", "scale": -1},
        ]

        result = stage1._validate_and_fix_scales(registers)

        self.assertEqual(result, registers)


class ExtractLiteralScaleTests(unittest.TestCase):
    def test_extracts_scales_from_expected_patterns(self) -> None:
        cases = {
            "Voltage x0.1 V": 0.1,
            "gain: 10": 10.0,
            "Gain 0.01": 0.01,
            "×100": 100.0,
            "no scale here": None,
        }

        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(stage1._extract_literal_scale(text), expected)


class ParseJsonArraySalvageTests(unittest.TestCase):
    def test_complete_array_matches_json_loads(self) -> None:
        text = '[{"address":"0x1000"},{"address":"0x1001"}]'

        result = stage1._parse_json_array_salvage(text)

        self.assertEqual(result, [{"address": "0x1000"}, {"address": "0x1001"}])

    def test_fenced_array_parses(self) -> None:
        text = '```json\n[{"address":"0x1000"}]\n```'

        result = stage1._parse_json_array_salvage(text)

        self.assertEqual(result, [{"address": "0x1000"}])

    def test_truncated_array_recovers_complete_leading_objects(self) -> None:
        text = (
            '[{"address":"0x1000","raw_name":"A"},'
            '{"address":"0x1001","raw_name":"B"},'
            '{"address":"0x1002","raw_name":"C'
        )

        result = stage1._parse_json_array_salvage(text)

        self.assertEqual(
            result,
            [
                {"address": "0x1000", "raw_name": "A"},
                {"address": "0x1001", "raw_name": "B"},
            ],
        )

    def test_garbage_without_array_returns_empty_list(self) -> None:
        self.assertEqual(stage1._parse_json_array_salvage("not json at all"), [])

    def test_truncated_first_object_returns_empty_list(self) -> None:
        text = '[{"address":"0x1000","raw_name":"A'

        result = stage1._parse_json_array_salvage(text)

        self.assertEqual(result, [])

class ChatWithOptionalThinkTests(unittest.TestCase):
    def test_accumulates_streamed_content_across_parts(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.calls = []

            def chat(self, **kwargs):
                self.calls.append(kwargs)
                return iter(
                    [
                        {"message": {"content": "Hello "}},
                        {"message": {"content": "stream"}},
                        {"message": {"content": " world"}},
                    ]
                )

        client = StubClient()

        result = stage1._chat_with_optional_think(client, lambda _msg, _level: None, ValueError, model="demo")

        self.assertEqual(result, {"message": {"content": "Hello stream world"}})
        self.assertEqual(client.calls, [{"think": False, "stream": True, "model": "demo"}])

    def test_retries_once_without_think_when_server_rejects_param(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.calls = []

            def chat(self, **kwargs):
                self.calls.append(kwargs)
                if "think" in kwargs:
                    raise TypeError("unexpected keyword argument 'think'")
                return iter(
                    [
                        {"message": {"content": "retry"}},
                        {"message": {"content": " ok"}},
                    ]
                )

        client = StubClient()
        logs = []

        result = stage1._chat_with_optional_think(
            client,
            lambda msg, level: logs.append((msg, level)),
            ValueError,
            model="demo",
        )

        self.assertEqual(result, {"message": {"content": "retry ok"}})
        self.assertEqual(
            client.calls,
            [
                {"think": False, "stream": True, "model": "demo"},
                {"stream": True, "model": "demo"},
            ],
        )
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][1], "warn")

class ScanAddressesInTextTests(unittest.TestCase):
    def test_scans_hex_and_modbus_reference_notation(self) -> None:
        text = "Row: 0x1037, holding 41000, input 30011, and duplicate 0X1037."

        self.assertEqual(stage1._scan_addresses_in_text(text), {0x1037, 999, 10})

    def test_ignores_non_matching_years_and_plain_numbers(self) -> None:
        text = "Released in 2026 with example 12345 and version 4.2026."

        self.assertEqual(stage1._scan_addresses_in_text(text), set())


if __name__ == "__main__":
    unittest.main()
