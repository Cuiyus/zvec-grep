import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

const repository = new URL("../../../", import.meta.url);
const workflow = await readFile(
  new URL(".github/workflows/retrieval-only.yml", repository),
  "utf8",
);
const action = await readFile(
  new URL(".github/actions/retrieval-authorize/action.yml", repository),
  "utf8",
);

// These bounded layout readers check our checked-in workflow contract. They are
// not YAML parsers; actionlint validates the complete workflow/action syntax.
function block(source, key, indent) {
  const lines = source.split("\n");
  const start = lines.findIndex(
    (line) => line === `${" ".repeat(indent)}${key}:`,
  );
  assert.notEqual(start, -1, `missing ${key} block`);
  const result = [];
  for (const line of lines.slice(start + 1)) {
    if (line.trim() && line.search(/\S/) <= indent) break;
    result.push(line);
  }
  return result.join("\n");
}

const jobs = Object.fromEntries(
  [...block(workflow, "jobs", 0).matchAll(/^ {2}([\w-]+):$/gm)].map((match) => [
    match[1],
    block(workflow, match[1], 2),
  ]),
);
function steps(job) {
  const starts = [...job.matchAll(/^ {6}- \S.*$/gm)];
  return starts.map((match, index) =>
    job.slice(match.index, starts[index + 1]?.index ?? job.length),
  );
}

test("Retrieval-only is one manual workflow and Semble is opt-in", async () => {
  assert.deepEqual(
    [...block(workflow, "on", 0).matchAll(/^ {2}([\w-]+):$/gm)].map(
      (match) => match[1],
    ),
    ["workflow_dispatch"],
  );
  assert.match(block(workflow, "run_semble", 6), /^ {8}type: boolean$/m);
  assert.match(block(workflow, "run_semble", 6), /^ {8}default: false$/m);
  const files = await readdir(new URL(".github/workflows/", repository));
  assert.deepEqual(
    files.filter((file) => /^retrieval.*\.ya?ml$/.test(file)),
    ["retrieval-only.yml"],
  );
  assert.match(jobs.semble, /^ {4}if: \$\{\{ inputs\.run_semble \}\}$/m);
  assert.match(jobs.semble, /^ {4}needs: quality-contract$/m);
});

test("every independently rerunnable job checks both actors before doing benchmark work", () => {
  assert.ok(Object.keys(jobs).length >= 7);
  assert.ok(jobs.results && jobs.retrieval && jobs.semble && jobs.authorize);
  for (const [name, job] of Object.entries(jobs)) {
    const entries = steps(job);
    assert.match(entries[0], /uses: actions\/checkout@/, `${name}: checkout`);
    assert.match(
      entries[1],
      /uses: \.\/\.github\/actions\/retrieval-authorize/,
      `${name}: authorization must precede setup, installation and execution`,
    );
    assert.match(entries[1], /^ {8}id: access$/m, `${name}: access step ID`);
    assert.doesNotMatch(entries[1], /continue-on-error|\bif:/);
    assert.equal(
      entries.filter((entry) =>
        entry.includes("uses: ./.github/actions/retrieval-authorize"),
      ).length,
      1,
      `${name}: exactly one authorization gate`,
    );
    for (const entry of entries.filter((entry) =>
      /if:.*always\(\)/.test(entry),
    ))
      assert.match(
        entry,
        /if:.*steps\.access\.outcome == 'success'/,
        `${name}: always() must not bypass denied permission`,
      );
  }
  assert.match(action, /DISPATCH_ACTOR: \$\{\{ github\.actor \}\}/);
  assert.match(action, /RERUN_ACTOR: \$\{\{ github\.triggering_actor \}\}/);
});

test("the optional baseline can skip while one final summary handles successful or failed upstream jobs", () => {
  assert.match(jobs.results, /^ {4}if:.*always\(\)/m);
  const finalSteps = steps(jobs.results);
  const optionalDownload = finalSteps.find((entry) =>
    entry.includes("name: Download optional Semble evidence"),
  );
  assert.ok(optionalDownload);
  assert.match(optionalDownload, /if:.*inputs\.run_semble/);
  assert.match(optionalDownload, /continue-on-error: true/);
  const builder = finalSteps.find((entry) => entry.includes("ci-report.mjs"));
  assert.ok(builder);
  assert.match(builder, /if:.*always\(\)/);
  assert.match(
    builder,
    /RETRIEVAL_SEMBLE_REQUESTED: \$\{\{ inputs\.run_semble \}\}/,
  );
  assert.match(builder, /RETRIEVAL_JOB_RESULTS: \$\{\{ toJSON\(needs\) \}\}/);
  const publishers = Object.entries(jobs).flatMap(([name, job]) =>
    steps(job)
      .filter((entry) => entry.includes("$GITHUB_STEP_SUMMARY"))
      .map((entry) => ({ name, entry })),
  );
  assert.equal(publishers.length, 1);
  assert.equal(publishers[0].name, "results");
  assert.match(publishers[0].entry, /if:.*always\(\)/);
  assert.match(publishers[0].entry, /summary\.md/);
  assert.match(publishers[0].entry, /missing results are not zero scores/);
});

const scriptStart = action.indexOf("        script: |\n");
assert.notEqual(scriptStart, -1);
const script = action
  .slice(scriptStart + "        script: |\n".length)
  .split("\n")
  .filter((line) => line.trim())
  .map((line) => {
    assert.ok(
      line.startsWith("          "),
      "unexpected authorization script indentation",
    );
    return line.slice(10);
  })
  .join("\n");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const authorize = new AsyncFunction(
  "github",
  "core",
  "context",
  "process",
  script,
);
const maintain = { permission: "write", role_name: "maintain" };
const admin = { permission: "admin", role_name: "admin" };

function attempt({
  eventName = "workflow_dispatch",
  dispatch = "maintainer",
  rerun = dispatch,
  roles = { maintainer: maintain },
  apiError,
} = {}) {
  const calls = [],
    messages = [],
    summaryCalls = [];
  const summary = {
    addHeading(value) {
      summaryCalls.push(["heading", value]);
      return this;
    },
    addRaw(value) {
      summaryCalls.push(["raw", value]);
      return this;
    },
    async write() {
      summaryCalls.push(["write"]);
    },
  };
  const github = {
    rest: {
      repos: {
        async getCollaboratorPermissionLevel(input) {
          calls.push(input);
          assert.deepEqual(
            { owner: input.owner, repo: input.repo },
            { owner: "owner", repo: "repo" },
          );
          if (apiError) throw apiError;
          return {
            data: roles[input.username] ?? {
              permission: "none",
              role_name: "none",
            },
          };
        },
      },
    },
  };
  return {
    calls,
    messages,
    summaryCalls,
    run: () =>
      authorize(
        github,
        { info: (message) => messages.push(message), summary },
        { eventName, repo: { owner: "owner", repo: "repo" } },
        { env: { DISPATCH_ACTOR: dispatch, RERUN_ACTOR: rerun } },
      ),
  };
}

function assertDeniedSummary(result) {
  assert.equal(
    result.summaryCalls.filter(([type]) => type === "write").length,
    1,
  );
  assert.deepEqual(result.summaryCalls[0], [
    "heading",
    "Retrieval-only: access denied",
  ]);
  assert.match(
    result.summaryCalls.find(([type]) => type === "raw")[1],
    /No results were produced by this job/,
  );
}

test("the embedded authorization script permits maintain and admin and de-duplicates the same actor", async () => {
  for (const role of [maintain, admin]) {
    const result = attempt({ roles: { maintainer: role } });
    await result.run();
    assert.equal(result.calls.length, 1);
    assert.equal(result.messages.length, 1);
    assert.deepEqual(result.summaryCalls, []);
  }
  const result = attempt({
    rerun: "administrator",
    roles: { maintainer: maintain, administrator: admin },
  });
  await result.run();
  assert.deepEqual(
    result.calls.map((call) => call.username),
    ["maintainer", "administrator"],
  );
});

for (const role of ["write", "triage", "read", "none"]) {
  test(`the embedded authorization script denies ${role} access and writes a denial summary`, async () => {
    const result = attempt({
      roles: {
        maintainer: {
          permission: role === "triage" ? "read" : role,
          role_name: role,
        },
      },
    });
    await assert.rejects(
      result.run(),
      /requires the maintain or admin repository role/,
    );
    assertDeniedSummary(result);
  });
}

test("a successful original maintainer cannot authorize a write-only actor's partial re-run", async () => {
  const result = attempt({
    rerun: "writer",
    roles: {
      maintainer: maintain,
      writer: { permission: "write", role_name: "write" },
    },
  });
  await assert.rejects(
    result.run(),
    /writer requires the maintain or admin repository role/,
  );
  assert.deepEqual(
    result.calls.map((call) => call.username),
    ["maintainer", "writer"],
  );
  assertDeniedSummary(result);
});

test("missing actors, nonmanual events and permission API failures are denied rather than allowed", async () => {
  for (const options of [{ dispatch: "" }, { rerun: "" }]) {
    const result = attempt(options);
    await assert.rejects(result.run(), /Missing workflow actor/);
    assertDeniedSummary(result);
  }
  for (const eventName of ["push", "pull_request", "workflow_run"]) {
    const result = attempt({ eventName });
    await assert.rejects(result.run(), /manual workflow_dispatch only/);
    assert.equal(result.calls.length, 0);
    assertDeniedSummary(result);
  }
  const apiError = new Error("permission API unavailable");
  const result = attempt({ apiError });
  await assert.rejects(result.run(), (error) => error === apiError);
  assertDeniedSummary(result);
});
