import assert from "node:assert/strict";
import {
  scoreResponse as evaluateResponse,
  VisibleFormatError,
  relativePath,
} from "../../core/response.mjs";
export { VisibleFormatError };

/** Enforce this benchmark's indexed search route without narrowing the public parser. */
export function validateSearchRoute(items, mode) {
  const allowed =
    mode === "hybrid"
      ? ["fts", "vector", "fts+vector"]
      : ["fts", "vector"].includes(mode)
        ? [mode]
        : null;
  assert.ok(allowed, `unknown search mode: ${String(mode)}`);
  assert.ok(Array.isArray(items), `${mode}: missing public parsed items`);
  for (const item of items)
    assert.ok(
      allowed.includes(item?.matched_by),
      `${mode}: unexpected matchedBy=${String(item?.matched_by)} at rank ${item?.rank ?? "?"}; expected ${allowed.join(" or ")}`,
    );
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
    fail("Expected the public search response's single text content block.");
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

/** Parse only the public, single-query MCP search text. Never consult hidden structuredContent. */
export function parseVisibleResponse(response) {
  const text = visibleText(response);
  if (text.includes("\x1b") || text.includes("\r"))
    fail("Unexpected terminal escapes or line framing.");
  const lines = text.split("\n");
  const first =
    /^freshness: (fresh|possibly_stale|served_from_current_index)$/.exec(
      lines.shift() ?? "",
    );
  if (!first) fail("Missing public MCP freshness header.");
  if (lines[0] === "results: served_from_current_index") {
    lines.shift();
    if (!/^background_refresh: \S.*$/.test(lines.shift() ?? ""))
      fail("Missing background refresh status.");
  } else if (lines[0]?.startsWith("background_refresh: ")) {
    if (first[1] !== "served_from_current_index")
      fail("Unexpected background refresh status.");
    lines.shift();
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
    if (line.startsWith("outline: ") && section === "metadata") {
      item.outline.push(line.slice("outline: ".length));
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
    if (line.startsWith("  ") && section === "source") {
      item.unnumbered_source.push(line.slice(2));
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

export function scoreResponse(response, gold, options = {}) {
  return evaluateResponse(response, gold, {
    parseResponse: parseVisibleResponse,
    ...options,
  });
}
