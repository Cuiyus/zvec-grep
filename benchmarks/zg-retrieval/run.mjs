import { main } from "./engines/zg/run.mjs";
import { runCli } from "./core/cli.mjs";

runCli(import.meta.url, main);
