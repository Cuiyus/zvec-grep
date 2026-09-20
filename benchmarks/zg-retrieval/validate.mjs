import { main } from "./core/validate.mjs";
import { runCli } from "./core/cli.mjs";

runCli(import.meta.url, main);
