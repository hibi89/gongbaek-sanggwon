from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / "배달상권분석_공백상권_V1.json"


class WorkflowDefinitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.nodes = {node["name"]: node for node in cls.workflow["nodes"]}

    def test_expected_nodes_are_connected_in_sequence(self) -> None:
        self.assertEqual(
            set(self.nodes),
            {
                "Manual Trigger",
                "Analysis Input",
                "Validate Analysis Input",
                "Kakao Address",
                "Validate Address and Coordinates",
                "Analyze 247 Categories",
                "Verify Results and Publication Gate",
            },
        )
        edge_count = sum(
            len(output)
            for connection in self.workflow["connections"].values()
            for output in connection.get("main", [])
        )
        self.assertEqual(edge_count, 6)

    def test_address_placeholder_is_rejected_before_geocoding(self) -> None:
        assignments = self.nodes["Analysis Input"]["parameters"]["assignments"]["assignments"]
        address = next(item["value"] for item in assignments if item["name"] == "address")
        code = self.nodes["Validate Analysis Input"]["parameters"]["jsCode"]
        self.assertEqual(address, "주소를 여기에 입력하세요")
        self.assertIn(address, code)

    def test_kakao_uses_header_credential_and_receives_address(self) -> None:
        parameters = self.nodes["Kakao Address"]["parameters"]
        self.assertEqual(parameters["authentication"], "genericCredentialType")
        self.assertEqual(parameters["genericAuthType"], "httpHeaderAuth")
        self.assertEqual(
            parameters["queryParameters"]["parameters"][0]["value"],
            "={{ $json.address }}",
        )

    def test_http_nodes_keep_status_codes_for_runtime_gates(self) -> None:
        for name in ("Kakao Address", "Analyze 247 Categories"):
            response = self.nodes[name]["parameters"]["options"]["response"]["response"]
            self.assertIs(response["fullResponse"], True)
        geocode_code = self.nodes["Validate Address and Coordinates"]["parameters"]["jsCode"]
        self.assertIn("statusCode", geocode_code)
        self.assertIn("kakao_http_status", geocode_code)
        gate_code = self.nodes["Verify Results and Publication Gate"]["parameters"]["jsCode"]
        self.assertIn("analyzer_http_status", gate_code)
        self.assertIn("unclassified_category_count", gate_code)
        self.assertIn("publication_gate", gate_code)

    def test_geocode_output_maps_coordinates_and_preserves_request_context(self) -> None:
        code = self.nodes["Validate Address and Coordinates"]["parameters"]["jsCode"]
        self.assertIn("Number(match.y)", code)
        self.assertIn("Number(match.x)", code)
        self.assertIn("matches.length !== 1", code)
        self.assertIn("request_id: input.request_id", code)
        self.assertIn("normalized_address: normalized", code)
        self.assertIn("geocode_match_count: matches.length", code)

    def test_analyzer_uses_header_auth_json_post_and_geocode_item(self) -> None:
        parameters = self.nodes["Analyze 247 Categories"]["parameters"]
        self.assertEqual(parameters["authentication"], "genericCredentialType")
        self.assertEqual(parameters["genericAuthType"], "httpHeaderAuth")
        self.assertEqual(parameters["method"], "POST")
        self.assertTrue(parameters["url"].startswith("http://127.0.0.1:"))
        self.assertIn("application/json", parameters["headerParameters"]["parameters"][0]["value"])
        self.assertIn("JSON.stringify($json)", parameters["jsonBody"])

    def test_export_contains_no_credentials_and_output_gate_checks_both_rankings(self) -> None:
        self.assertTrue(all(not node.get("credentials") for node in self.workflow["nodes"]))
        code = self.nodes["Verify Results and Publication Gate"]["parameters"]["jsCode"]
        self.assertIn("overall_top20.length !== 20", code)
        self.assertIn("a_top10.length !== 10", code)
        self.assertIn("geocode_match_count !== 1", code)
        self.assertIn("result.publishable", code)


if __name__ == "__main__":
    unittest.main()
