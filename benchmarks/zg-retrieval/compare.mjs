import { main } from "./reports/compare.mjs";
import { runCli } from "./core/cli.mjs";

runCli(import.meta.url, main);
