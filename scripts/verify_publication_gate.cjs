#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const workflowPath = path.join(projectRoot, "배달상권분석_공백상권_V1.json");
const workflow = JSON.parse(fs.readFileSync(workflowPath, "utf8"));
const gateNode = workflow.nodes.find(
  (candidate) => candidate.name === "Verify Results and Publication Gate",
);

if (typeof gateNode?.parameters?.jsCode !== "string") {
  throw new Error("Publication gate JavaScript was not found in the V1 workflow.");
}

// Execute the exact Code node body in its normal function wrapper with a mocked
// n8n $input object. This checks the gate contract without starting a workflow.
const gate = new Function("$input", gateNode.parameters.jsCode);
const valid = {
  geocode_match_count: 1,
  status: "analysis_complete",
  publishable: true,
  publication_gate: "passed",
  coverage: { category_count: 247, unclassified_category_count: 0 },
  overall_top20: Array.from({ length: 20 }, (_, index) => ({ rank: index + 1 })),
  a_top10: Array.from({ length: 10 }, (_, index) => ({ rank: index + 1 })),
};

function run(body, statusCode = 200) {
  const envelope = { statusCode, body };
  return gate({ first: () => ({ json: envelope }) });
}

const accepted = run(valid);
assert.equal(accepted[0].json.operator_action, "고객 전달 검토 가능");

const rejectedCases = [
  ["geocode_match_count_zero", { ...valid, geocode_match_count: 0 }, 200],
  ["geocode_match_count_multiple", { ...valid, geocode_match_count: 2 }, 200],
  ["analyzer_http_error", valid, 503],
  ["analysis_incomplete", { ...valid, status: "analysis_failed" }, 200],
  [
    "publication_gate_failed",
    { ...valid, publishable: false, publication_gate: "blocked" },
    200,
  ],
  [
    "category_coverage_incomplete",
    { ...valid, coverage: { category_count: 246, unclassified_category_count: 0 } },
    200,
  ],
  [
    "unclassified_category",
    { ...valid, coverage: { category_count: 247, unclassified_category_count: 1 } },
    200,
  ],
  ["overall_top20_incomplete", { ...valid, overall_top20: valid.overall_top20.slice(0, 19) }, 200],
  ["a_top10_incomplete", { ...valid, a_top10: valid.a_top10.slice(0, 9) }, 200],
];

for (const [name, body, statusCode] of rejectedCases) {
  assert.throws(() => run(body, statusCode), undefined, `${name} must be rejected`);
}

process.stdout.write(
  JSON.stringify({
    workflow: path.basename(workflowPath),
    gateNode: gateNode.name,
    acceptedValidFixture: true,
    rejectedCases: rejectedCases.map(([name]) => name),
    caseCount: rejectedCases.length + 1,
    liveN8nExecution: false,
  }) + "\n",
);
