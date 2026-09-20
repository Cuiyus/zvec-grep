import { main } from "./reports/ci.mjs";
import { runCli } from "./core/cli.mjs";

runCli(import.meta.url, main);
