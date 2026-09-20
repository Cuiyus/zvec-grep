import { createHash } from "node:crypto";

const sha256 = (text) =>
  createHash("sha256").update(text, "utf8").digest("hex");
const HEX = /^[a-f0-9]{64}$/;

export class VisibleFormatError extends Error {
  constructor(message) {
    super(message);
    this.name = "VisibleFormatError";
    this.code = "format_unknown";
  }
}

export function relativePath(value) {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    !value.startsWith("/") &&
    !value.includes("\\") &&
    !/[\x00-\x1f]/.test(value) &&
    !value
      .split("/")
      .some((part) => part === ".." || part === "." || part === "")
  );
}

function anchorLines(anchor) {
  const lines = anchor.text.split(/\r?\n/);
  if (lines.at(-1) === "") lines.pop();
  return lines;
}

function validateGold(gold) {
  if (
    !gold ||
    gold.schema_version !== 1 ||
    !["reviewed", "unknown", "disputed"].includes(gold.status)
  )
    throw new Error("Invalid Gold schema/status.");
  if (gold.status !== "reviewed") return;
  if (
    !Array.isArray(gold.targets) ||
    !gold.targets.some((t) => t.role === "accepted")
  )
    throw new Error("Reviewed Gold needs an accepted target.");
  const ids = new Set();
  for (const target of gold.targets) {
    if (
      !target.id ||
      ids.has(target.id) ||
      !relativePath(target.path) ||
      !["symbol", "code_span"].includes(target.kind) ||
      !["accepted", "bridge"].includes(target.role) ||
      !HEX.test(target.source_sha256 ?? "") ||
      !Array.isArray(target.anchors) ||
      !target.anchors.length
    )
      throw new Error("Invalid Gold target.");
    ids.add(target.id);
    for (const anchor of target.anchors) {
      if (
        typeof anchor.text !== "string" ||
        !anchor.text.trim() ||
        !Number.isSafeInteger(anchor.start_line) ||
        anchor.start_line < 1 ||
        !Number.isSafeInteger(anchor.end_line) ||
        anchor.end_line < anchor.start_line ||
        anchorLines(anchor).length !==
          anchor.end_line - anchor.start_line + 1 ||
        sha256(anchor.text) !== anchor.sha256
      )
        throw new Error("Invalid Gold anchor/hash/line span.");
    }
  }
  if (gold.ndcg?.enabled === true) {
    if (!Array.isArray(gold.ndcg.groups) || !gold.ndcg.groups.length)
      throw new Error("Enabled nDCG requires relevance groups.");
    const accepted = new Set(
      gold.targets.filter((t) => t.role === "accepted").map((t) => t.id),
    );
    const groups = new Set();
    for (const group of gold.ndcg.groups) {
      if (
        !group.id ||
        groups.has(group.id) ||
        !Array.isArray(group.target_ids) ||
        !group.target_ids.length ||
        new Set(group.target_ids).size !== group.target_ids.length ||
        group.target_ids.some((id) => !accepted.has(id))
      )
        throw new Error("Invalid nDCG relevance group.");
      groups.add(group.id);
    }
  }
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(",")}}`;
  return JSON.stringify(value);
}

/** Parse saved public output and establish scoring eligibility. File metrics are computed separately. */
export function scoreResponse(response, gold, { parseResponse }) {
  const result = {
    status: "harness_invalid",
    execution_status: "unknown",
    gold_status: gold?.status ?? null,
    items: [],
    visible_output_sha256: null,
    structured_output_sha256: null,
    ranking_sha256: null,
    invalid_reason: null,
  };
  if (
    Array.isArray(response?.content) &&
    response.content.every(
      (b) => b.type === "text" && typeof b.text === "string",
    )
  ) {
    result.visible_output_sha256 = sha256(
      response.content.map((b) => b.text).join("\n"),
    );
  }
  if (response?.structuredContent !== undefined)
    result.structured_output_sha256 = sha256(
      stableJson(response.structuredContent),
    );
  try {
    validateGold(gold);
  } catch (error) {
    result.invalid_reason = `gold_invalid: ${error.message}`;
    return result;
  }
  let productError = response?.isError === true;
  let parsed;
  if (!productError) {
    try {
      parsed = parseResponse(response);
    } catch (error) {
      result.invalid_reason = `format_unknown: ${error.message}`;
      return result;
    }
    // Some public tools return a documented product failure as plain text with
    // isError unset. Adapters must recognize that exact format explicitly.
    productError = parsed.execution_status === "product_error";
    if (productError) result.product_error_reason = parsed.product_error_reason;
  }
  if (parsed && !productError) {
    result.items = parsed.items;
    result.freshness = parsed.freshness;
    result.empty_reason = parsed.empty_reason;
    result.ranking_identity_scope =
      "visible path/range/matched range/source locations/leading outline; hidden entity IDs unavailable";
    result.ranking_sha256 = sha256(
      stableJson(
        parsed.items.map((item) => ({
          rank: item.rank,
          path: item.path,
          range: item.range,
          matched_range: item.matched_range,
          source_locations: item.source_lines.map((line) => line.line),
          leading_outline: item.outline.find((line) => line.trim()) ?? null,
        })),
      ),
    );
  }
  result.execution_status = productError ? "product_error" : "success";
  if (gold.status !== "reviewed") {
    result.status = `gold_${gold.status}`;
    return result;
  }
  result.status = productError ? "product_error" : "scored";

  return result;
}
