import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

export function runCli(url, main) {
  if (process.argv[1] && url === pathToFileURL(resolve(process.argv[1])).href) {
    Promise.resolve()
      .then(() => main())
      .catch((error) => {
        console.error(error);
        process.exitCode = 1;
      });
  }
}
