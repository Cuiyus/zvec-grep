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

function fail(message) {
  throw new VisibleFormatError(message);
}

function visibleText(response) {
  if (
    !response ||
    !Array.isArray(response.content) ||
    response.content.length !== 1 ||
    response.content[0]?.type !== "text" ||
    typeof response.content[0].text !== "string"
  ) {
    fail("Expected the official search response's single text content block.");
  }
  return response.content[0].text;
}

function parseRange(text) {
  const match = /^([1-9]\d*)(?:-([1-9]\d*))?$/.exec(text);
  if (match) {
    const start = Number(match[1]);
    const end = Number(match[2] ?? match[1]);
    if (
      !Number.isSafeInteger(start) ||
      !Number.isSafeInteger(end) ||
      end < start
    )
      fail("Invalid source range.");
    return { kind: "text", start_line: start, end_line: end };
  }
  if (/^(?:file|page:[1-9]\d*|bytes:\d+-\d+)$/.test(text))
    return { kind: "other", label: text };
  fail(`Unknown range: ${text}`);
}

function relativePath(value) {
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

/** Parse only the public, single-query MCP search text. Never consult hidden structuredContent. */
export function parseVisibleResponse(response) {
  const text = visibleText(response);
  if (text.includes("\x1b") || text.includes("\r"))
    fail("Unexpected terminal escapes or line framing.");
  const lines = text.split("\n");
  const first = /^freshness: (fresh|possibly_stale)$/.exec(lines.shift() ?? "");
  if (!first) fail("Missing public MCP freshness header.");
  if (lines[0] === "results: served_from_current_index") {
    lines.shift();
    if (!/^background_refresh: \S.*$/.test(lines.shift() ?? ""))
      fail("Missing background refresh status.");
  }
  // An empty string is not a successful empty search. The product has explicit empty labels.
  if (lines[0] === "No matches." || lines[0] === "No searchable files.") {
    const emptyReason =
      lines.shift() === "No matches." ? "no_matches" : "no_searchable_files";
    if (lines[0]?.startsWith("missing: ")) lines.shift();
    if (lines.some((line) => line !== ""))
      fail("Unexpected text after empty result.");
    return { text, items: [], freshness: first[1], empty_reason: emptyReason };
  }
  const items = [];
  let item;
  let section = "metadata";
  const header =
    /^#([1-9]\d*)(?: \[(?:group_coverage: [^\]\n]+|global_fill)\])? matchedBy=(fts\+vector|fts|vector|lexical)(?: score=(-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?))? (.+?):((?:[1-9]\d*(?:-[1-9]\d*)?)|file|page:[1-9]\d*|bytes:\d+-\d+)$/;
  for (const line of lines) {
    const match = header.exec(line);
    if (match) {
      const rank = Number(match[1]);
      if (!Number.isSafeInteger(rank) || rank !== items.length + 1 || rank > 10)
        fail("Expected consecutive native top-10 ranks.");
      if (!relativePath(match[4]))
        fail("Expected an unambiguous repository-relative path.");
      item = {
        rank,
        path: match[4],
        range: parseRange(match[5]),
        matched_by: match[2],
        header: line,
        metadata: [],
        outline: [],
        source_lines: [],
        unnumbered_source: [],
        raw_lines: [line],
        matched_range: null,
      };
      items.push(item);
      section = "metadata";
      continue;
    }
    if (!item) {
      if (line === "") continue;
      fail("Unknown public search output before the first ranked item.");
    }
    item.raw_lines.push(line);
    if (line === "source:") {
      section = "source";
      continue;
    }
    if (line === "outline:" && section === "metadata") {
      section = "outline";
      continue;
    }
    if (/^matched: /.test(line)) {
      item.matched_range = parseRange(line.slice(9));
      continue;
    }
    if (
      section === "metadata" &&
      /^(?:groups: |status: possibly_stale$|symbol: |heading: |heading_level: \d+$|scope: )/.test(
        line,
      )
    ) {
      item.metadata.push(line);
      continue;
    }
    const source = /^([1-9]\d*)(?::|-)?\t(.*)$/.exec(line);
    if (source) {
      const number = Number(source[1]);
      if (
        !Number.isSafeInteger(number) ||
        number <= (item.source_lines.at(-1)?.line ?? 0)
      )
        fail("Repeated or unordered visible source lines.");
      if (
        item.range.kind === "text" &&
        (number < item.range.start_line || number > item.range.end_line)
      )
        fail("Visible source falls outside the item's public range.");
      item.source_lines.push({ line: number, text: source[2] });
      section = "source";
      continue;
    }
    if (
      line.startsWith("\t") &&
      item.range.kind === "other" &&
      section !== "outline"
    ) {
      item.unnumbered_source.push(line.slice(1));
      section = "source";
      continue;
    }
    if (line === "" || line === "...") {
      if (section === "outline") item.outline.push(line);
      continue;
    }
    if (section === "outline") {
      item.outline.push(line);
      continue;
    }
    fail(`Unknown line in result #${item.rank}: ${line.slice(0, 80)}`);
  }
  if (items.length === 0)
    fail("Missing explicit empty result or ranked items.");
  return { text, items, freshness: first[1], empty_reason: null };
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
export function scoreResponse(
  response,
  gold,
  { parseResponse = parseVisibleResponse } = {},
) {
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
