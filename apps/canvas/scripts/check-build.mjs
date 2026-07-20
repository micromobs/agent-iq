import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const expectedRoot = resolve(packageRoot, "../../.github/extensions/agent-iq-canvas/web");
const temporaryRoot = mkdtempSync(join(tmpdir(), "agent-iq-canvas-"));
const vite = resolve(packageRoot, "node_modules/vite/bin/vite.js");

function files(root) {
  const found = [];
  const visit = (directory) => {
    for (const name of readdirSync(directory)) {
      const path = join(directory, name);
      if (statSync(path).isDirectory()) visit(path);
      else found.push(relative(root, path).replaceAll("\\", "/"));
    }
  };
  visit(root);
  return found.sort();
}

try {
  execFileSync(process.execPath, [vite, "build", "--outDir", temporaryRoot, "--emptyOutDir"], {
    cwd: packageRoot,
    stdio: "inherit",
  });
  const expectedFiles = files(expectedRoot);
  const actualFiles = files(temporaryRoot);
  if (JSON.stringify(expectedFiles) !== JSON.stringify(actualFiles)) {
    throw new Error("Canvas web build file list is stale.");
  }
  for (const path of expectedFiles) {
    const expected = readFileSync(join(expectedRoot, path));
    const actual = readFileSync(join(temporaryRoot, path));
    if (!expected.equals(actual)) {
      throw new Error(`Canvas web build is stale: ${path}`);
    }
  }
  console.log(
    `Canvas web build is deterministic: ${actualFiles.length} files in ${basename(expectedRoot)}`,
  );
} finally {
  rmSync(temporaryRoot, { force: true, recursive: true });
}
