import { main } from "./engines/semble/report.mjs";
import { runCli } from "./core/cli.mjs";

runCli(import.meta.url, main);
