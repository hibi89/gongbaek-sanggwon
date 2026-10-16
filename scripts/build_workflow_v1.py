#!/usr/bin/env python3
"""Create an importable, credential-clean n8n workflow for local gap analysis."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_WORKFLOW = ROOT / "배달상권분석_공백상권_V1.json"
ANALYZER_URL = os.environ.get("GAP_ANALYZER_URL", "http://127.0.0.1:8765/analyze")


def node(name: str, node_type: str, version: float, position: list[int], parameters: dict) -> dict:
    return {
        "parameters": parameters,
        "id": str(uuid.uuid4()),
        "name": name,
        "type": node_type,
        "typeVersion": version,
        "position": position,
    }


def main() -> int:
    address = "주소를 여기에 입력하세요"

    manual = node(
        "Manual Trigger",
        "n8n-nodes-base.manualTrigger",
        1,
        [220, 300],
        {},
    )
    input_node = node(
        "Analysis Input",
        "n8n-nodes-base.set",
        3.5,
        [440, 300],
        {
            "assignments": {
                "assignments": [
                    {"id": str(uuid.uuid4()), "name": "address", "value": address, "type": "string"},
                    {"id": str(uuid.uuid4()), "name": "request_id", "value": "={{ 'GAP-' + $execution.id }}", "type": "string"},
                ]
            },
            "options": {},
        },
    )
    validate_input = node(
        "Validate Analysis Input",
        "n8n-nodes-base.code",
        2,
        [650, 300],
        {
            "mode": "runOnceForAllItems",
            "language": "javaScript",
            "jsCode": "const item = $input.first().json;\n"
            "const address = String(item.address || '').trim();\n"
            "if (!address || address === '주소를 여기에 입력하세요') throw new Error('Analysis Input 노드에 분석 주소 한 곳을 입력해 주세요.');\n"
            "return [{ json: { ...item, address } }];",
        },
    )
    kakao = node(
        "Kakao Address",
        "n8n-nodes-base.httpRequest",
        4.5,
        [860, 300],
        {
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "url": "https://dapi.kakao.com/v2/local/search/address.json",
            "sendQuery": True,
            "queryParameters": {
                "parameters": [
                    {"name": "query", "value": "={{ $json.address }}"}
                ]
            },
            "options": {"response": {"response": {"fullResponse": True, "responseFormat": "json"}}},
        },
    )
    normalize = node(
        "Validate Address and Coordinates",
        "n8n-nodes-base.code",
        2,
        [1100, 300],
        {
            "mode": "runOnceForAllItems",
            "language": "javaScript",
            "jsCode": "const envelope = $input.first().json;\n"
            "const input = $('Analysis Input').first().json;\n"
            "const response = envelope && envelope.body && typeof envelope.body === 'object' ? envelope.body : envelope;\n"
            "const statusCode = envelope && envelope.statusCode != null ? Number(envelope.statusCode) : null;\n"
            "if (statusCode !== null && (!Number.isFinite(statusCode) || statusCode < 200 || statusCode >= 300)) throw new Error('Kakao address lookup failed with HTTP ' + statusCode + '.');\n"
            "const matches = Array.isArray(response.documents) ? response.documents : [];\n"
            "if (matches.length !== 1) throw new Error('Kakao 주소 검색 결과가 ' + matches.length + '건입니다. 정확히 한 곳으로 좁혀지는 주소를 입력해 주세요.');\n"
            "const match = matches[0];\n"
            "const latitude = Number(match.y);\n"
            "const longitude = Number(match.x);\n"
            "if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) throw new Error('주소 변환 결과의 좌표가 올바르지 않습니다.');\n"
            "const normalized = match.road_address?.address_name || match.address?.address_name || match.address_name || input.address;\n"
            "return [{ json: { request_id: input.request_id, address: input.address, normalized_address: normalized, latitude, longitude, geocode_match_count: matches.length, kakao_http_status: statusCode } }];",
        },
    )
    analyze = node(
        "Analyze 247 Categories",
        "n8n-nodes-base.httpRequest",
        4.5,
        [1380, 300],
        {
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "method": "POST",
            "url": ANALYZER_URL,
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json) }}",
            "options": {"response": {"response": {"fullResponse": True, "responseFormat": "json"}}},
        },
    )
    output = node(
        "Verify Results and Publication Gate",
        "n8n-nodes-base.code",
        2,
        [1660, 300],
        {
            "mode": "runOnceForAllItems",
            "language": "javaScript",
            "jsCode": "const envelope = $input.first().json;\n"
            "const result = envelope && envelope.body && typeof envelope.body === 'object' ? envelope.body : envelope;\n"
            "const statusCode = envelope && envelope.statusCode != null ? Number(envelope.statusCode) : null;\n"
            "if (statusCode !== null && (!Number.isFinite(statusCode) || statusCode < 200 || statusCode >= 300)) throw new Error('Analyzer request failed with HTTP ' + statusCode + '.');\n"
            "if (result.geocode_match_count !== 1) throw new Error('정확히 1건의 주소 매치가 확인되지 않아 분석 결과를 차단했습니다.');\n"
            "if (result.status !== 'analysis_complete') throw new Error('분석이 완전히 끝나지 않았습니다. 결과를 고객에게 전달하지 마세요.');\n"
            "if (result.publishable !== true || result.publication_gate !== 'passed') throw new Error('publication gate를 통과하지 못했습니다. 결과를 고객에게 전달하지 마세요.');\n"
            "if (!result.coverage || result.coverage.category_count !== 247 || result.coverage.unclassified_category_count !== 0) throw new Error('247개 업종 전체 검증 또는 분류 완전성 조건을 충족하지 못했습니다.');\n"
            "if (!Array.isArray(result.overall_top20) || result.overall_top20.length !== 20) throw new Error('전체 TOP20 결과가 완전하지 않습니다.');\n"
            "if (!Array.isArray(result.a_top10) || result.a_top10.length !== 10) throw new Error('A형 TOP10 결과가 완전하지 않습니다.');\n"
            "return [{ json: { ...result, analyzer_http_status: statusCode, operator_action: '고객 전달 검토 가능' } }];",
        },
    )

    nodes = [manual, input_node, validate_input, kakao, normalize, analyze, output]
    connections = {
        "Manual Trigger": {"main": [[{"node": "Analysis Input", "type": "main", "index": 0}]]},
        "Analysis Input": {"main": [[{"node": "Validate Analysis Input", "type": "main", "index": 0}]]},
        "Validate Analysis Input": {"main": [[{"node": "Kakao Address", "type": "main", "index": 0}]]},
        "Kakao Address": {"main": [[{"node": "Validate Address and Coordinates", "type": "main", "index": 0}]]},
        "Validate Address and Coordinates": {"main": [[{"node": "Analyze 247 Categories", "type": "main", "index": 0}]]},
        "Analyze 247 Categories": {"main": [[{"node": "Verify Results and Publication Gate", "type": "main", "index": 0}]]},
    }
    workflow = {
        "name": "공백상권 분석 V1 - 247개 업종",
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1"},
        "versionId": str(uuid.uuid4()),
        "meta": {"credentialsConfigured": False},
        "tags": [],
    }
    OUTPUT_WORKFLOW.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(OUTPUT_WORKFLOW.relative_to(ROOT)),
        "nodes": [item["name"] for item in nodes],
        "active": workflow["active"],
        "credentials_included": False,
        "analyzer_url": ANALYZER_URL,
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
