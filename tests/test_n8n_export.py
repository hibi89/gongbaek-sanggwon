from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import deliver_from_n8n_execution as exporter
from scripts.deliver_from_n8n_execution import (
    decode_flatted,
    extract_gate_result,
    extract_node_result,
    validate_execution_outputs,
)


ROOT = Path(__file__).resolve().parents[1]


class N8nExportTests(unittest.TestCase):
    @staticmethod
    def execution_fixture(
        *,
        kakao_status: int = 200,
        coordinate_kakao_status: int = 200,
        analyzer_status: int = 200,
        gate_analyzer_status: int = 200,
        geocode_match_count: int = 1,
    ) -> dict:
        def node(payload: dict) -> list[dict]:
            return [{"data": {"main": [[{"json": payload}] ]}}]

        return {
            "resultData": {
                "lastNodeExecuted": "Verify Results and Publication Gate",
                "runData": {
                    "Kakao Address": node({"statusCode": kakao_status}),
                    "Validate Address and Coordinates": node({
                        "kakao_http_status": coordinate_kakao_status,
                        "geocode_match_count": geocode_match_count,
                    }),
                    "Analyze 247 Categories": node({"statusCode": analyzer_status}),
                    "Verify Results and Publication Gate": node({
                        "analyzer_http_status": gate_analyzer_status,
                        "geocode_match_count": geocode_match_count,
                    }),
                },
            }
        }

    def test_decodes_nested_flatted_values(self) -> None:
        value = decode_flatted('[{"name":"1","child":"2"},"hello",{"label":"3"},"world"]')
        self.assertEqual(value, {"name": "hello", "child": {"label": "world"}})

    def test_preserves_cycles(self) -> None:
        value = decode_flatted('[{"self":"0"}]')
        self.assertIs(value["self"], value)

    def test_rejects_invalid_flatted_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid reference"):
            decode_flatted('[{"missing":"4"}]')

    def test_extracts_final_publication_gate_result(self) -> None:
        execution = {
            "resultData": {
                "lastNodeExecuted": "Verify Results and Publication Gate",
                "runData": {
                    "Verify Results and Publication Gate": [
                        {
                            "data": {
                                "main": [
                                    [
                                        {
                                            "json": {
                                                "status": "analysis_complete",
                                                "publication_gate": "passed",
                                            }
                                        }
                                    ]
                                ]
                            }
                        }
                    ]
                },
            }
        }
        self.assertEqual(
            extract_gate_result(execution),
            {"status": "analysis_complete", "publication_gate": "passed"},
        )

    def test_rejects_execution_that_did_not_reach_gate(self) -> None:
        with self.assertRaisesRegex(ValueError, "publication gate node"):
            extract_gate_result({"resultData": {"lastNodeExecuted": "Analyze 247 Categories"}})

    def test_extracts_http_node_response_for_direct_status_validation(self) -> None:
        execution = {
            "resultData": {
                "runData": {
                    "Kakao Address": [
                        {"data": {"main": [[{"json": {"statusCode": 200, "statusMessage": "OK"}}]]}}
                    ]
                }
            }
        }
        self.assertEqual(
            extract_node_result(execution, "Kakao Address"),
            {"statusCode": 200, "statusMessage": "OK"},
        )

    def test_validates_each_http_response_and_coordinate_match(self) -> None:
        result, statuses = validate_execution_outputs(self.execution_fixture())
        self.assertEqual(result["geocode_match_count"], 1)
        self.assertEqual(statuses, {"kakao_http_status": 200, "analyzer_http_status": 200})

    def test_rejects_failed_http_statuses_and_missing_geocode(self) -> None:
        cases = [
            ({"kakao_status": 503}, "Kakao"),
            ({"coordinate_kakao_status": 500}, "Kakao"),
            ({"analyzer_status": 502}, "Analyzer"),
            ({"gate_analyzer_status": 504}, "Analyzer"),
            ({"geocode_match_count": 0}, "valid match"),
            ({"geocode_match_count": 2}, "valid match"),
        ]
        for overrides, expected_message in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, expected_message):
                    validate_execution_outputs(self.execution_fixture(**overrides))

    def test_delivery_to_localappdata_root_returns_absolute_path(self) -> None:
        result = json.loads(
            (ROOT / "data" / "results" / "sample_workflow_address_taxonomy_v3_20260930.json")
            .read_text(encoding="utf-8")
        )
        execution = {
            "execution_id": 271,
            "mode": "manual",
            "status": "success",
            "kakao_http_status": 200,
            "analyzer_http_status": 200,
        }
        with tempfile.TemporaryDirectory() as temporary:
            delivery_root = Path(temporary) / "GapAnalysis" / "deliveries"
            output_dir = delivery_root / "GAP-export-test"
            with patch.object(exporter, "DEFAULT_DELIVERY_ROOT", delivery_root):
                summary = exporter.generate_delivery(result, execution, output_dir)
            self.assertEqual(summary["output_dir"], str(output_dir.resolve()))
            self.assertTrue((output_dir / "manifest.json").is_file())
            self.assertEqual([item["bytes"] > 0 for item in summary["files"]], [True, True])


if __name__ == "__main__":
    unittest.main()
