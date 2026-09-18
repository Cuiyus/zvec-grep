import { scoreResponse, VisibleFormatError } from "./scoring.mjs";

function fail(message) {
  throw new VisibleFormatError(message);
}

function exactKeys(value, keys) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).sort().join("\0") === [...keys].sort().join("\0")
  );
}

function relativePath(value) {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    !value.startsWith("/") &&
    !value.includes("\\") &&
    !/^[a-zA-Z]:/.test(value) &&
    !/[\x00-\x1f\x7f]/.test(value) &&
    !value
      .split("/")
      .some((part) => part === ".." || part === "." || part === "")
  );
}

/**
 * Parse Semble @0051e000's public MCP search response for the fixed protocol:
 * one repository, top_k=10, max_snippet_lines=10, content="all".
 * utils.format_results returns the first ten splitlines() without stripping,
 * outlining, or reordering them. Map ONLY that visible snippet, never the rest
 * of the chunk's advertised range. Source/chunk validation belongs to the
 * runner: Python splitlines also recognizes separators other than LF, so the
 * source audit must verify line mapping rather than filling missing content.
 */
export function createSembleResponseParser({ expectedQuery } = {}) {
  if (typeof expectedQuery !== "string" || expectedQuery.length === 0)
    throw new Error("Semble scoring requires the exact original query.");
  return function parseSembleResponse(response) {
    if (
      !Array.isArray(response?.content) ||
      response.content.length !== 1 ||
      response.content[0]?.type !== "text" ||
      typeof response.content[0].text !== "string"
    )
      fail("Expected Semble's single public MCP text content block.");
    const text = response.content[0].text;
    // mcp.search catches indexing ValueError and returns this string directly;
    // FastMCP consequently does not set isError on this product failure.
    if (/^Failed to index ['"].+['"]: [\s\S]+$/.test(text))
      return {
        text,
        execution_status: "product_error",
        product_error_reason: text,
      };
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      fail("Expected Semble search JSON or its explicit indexing error.");
    }
    if (exactKeys(payload, ["error"]) && payload.error === "No results found.")
      return {
        text,
        items: [],
        freshness: null,
        empty_reason: "no_results",
      };
    if (!exactKeys(payload, ["query", "results"]))
      fail("Unknown Semble single-repository search response fields.");
    if (payload.query !== expectedQuery)
      fail("Semble response query differs from the exact original query.");
    if (
      !Array.isArray(payload.results) ||
      payload.results.length < 1 ||
      payload.results.length > 10
    )
      fail(
        "Expected one to ten native results or the explicit empty response.",
      );
    const items = payload.results.map((entry, index) => {
      if (
        !exactKeys(entry, [
          "file_path",
          "start_line",
          "end_line",
          "score",
          "content",
        ])
      )
        fail("Unknown Semble result fields or missing visible snippet.");
      if (!relativePath(entry.file_path))
        fail("Expected an unambiguous repository-relative Semble path.");
      if (
        !Number.isSafeInteger(entry.start_line) ||
        !Number.isSafeInteger(entry.end_line) ||
        entry.start_line < 1 ||
        entry.end_line < entry.start_line
      )
        fail("Invalid Semble source range.");
      if (typeof entry.score !== "number" || !Number.isFinite(entry.score))
        fail("Invalid Semble retrieval score.");
      if (
        typeof entry.content !== "string" ||
        /[\x00-\x08\x0b-\x1f\x7f\x85\u2028\u2029]/.test(entry.content)
      )
        fail("Unknown Semble snippet content or line framing.");
      const lines = entry.content === "" ? [] : entry.content.split("\n");
      if (
        lines.length > 10 ||
        lines.length > entry.end_line - entry.start_line + 1
      )
        fail("Semble visible snippet exceeds its limit or source range.");
      return {
        rank: index + 1,
        path: entry.file_path,
        range: {
          kind: "text",
          start_line: entry.start_line,
          end_line: entry.end_line,
        },
        matched_by: "semble",
        header: JSON.stringify(entry),
        metadata: [],
        outline: [],
        source_lines: lines.map((line, offset) => ({
          line: entry.start_line + offset,
          text: line,
        })),
        unnumbered_source: [],
        raw_lines: [JSON.stringify(entry)],
        matched_range: null,
      };
    });
    return { text, items, freshness: null, empty_reason: null };
  };
}

/** Keep Gold matching, native rank Hit/MRR, and grouped nDCG shared with zg. */
export function scoreSembleResponse(response, gold, { expectedQuery } = {}) {
  return scoreResponse(response, gold, {
    parseResponse: createSembleResponseParser({ expectedQuery }),
  });
}
