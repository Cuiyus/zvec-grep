import { main } from "./engines/semble/run.mjs";
import { runCli } from "./core/cli.mjs";

runCli(import.meta.url, main);
