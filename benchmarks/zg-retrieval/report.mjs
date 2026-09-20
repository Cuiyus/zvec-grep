import { main } from "./engines/zg/report.mjs";
import { runCli } from "./core/cli.mjs";

runCli(import.meta.url, main);
