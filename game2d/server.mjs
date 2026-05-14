import { createReadStream, existsSync, statSync } from "node:fs";
import { extname, join, normalize, resolve } from "node:path";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";

const MIME_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".pdf": "application/pdf",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8"
};

function parseArgs(argv) {
  const options = { host: "127.0.0.1", port: 4173 };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--host" && argv[index + 1]) {
      options.host = argv[index + 1];
      index += 1;
    } else if (arg === "--port" && argv[index + 1]) {
      options.port = Number(argv[index + 1]);
      index += 1;
    }
  }
  return options;
}

const options = parseArgs(process.argv.slice(2));
const repoRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));

function send404(response) {
  response.statusCode = 404;
  response.setHeader("Content-Type", "text/plain; charset=utf-8");
  response.end("Not found");
}

function safePathFromUrl(urlPath) {
  const cleanPath = decodeURIComponent(urlPath.split("?")[0]);
  const relativePath = cleanPath === "/" ? "game2d/index.html" : normalize(cleanPath.replace(/^\/+/, ""));
  const absolutePath = resolve(join(repoRoot, relativePath));
  if (!absolutePath.startsWith(repoRoot)) {
    return null;
  }
  return absolutePath;
}

const server = createServer((request, response) => {
  const absolutePath = safePathFromUrl(request.url ?? "/");
  if (!absolutePath) {
    send404(response);
    return;
  }

  let filePath = absolutePath;
  if (existsSync(filePath) && statSync(filePath).isDirectory()) {
    filePath = join(filePath, "index.html");
  }

  if (!existsSync(filePath)) {
    send404(response);
    return;
  }

  const extension = extname(filePath).toLowerCase();
  response.statusCode = 200;
  response.setHeader("Cache-Control", "no-store");
  response.setHeader("Content-Type", MIME_TYPES[extension] ?? "application/octet-stream");
  createReadStream(filePath).pipe(response);
});

server.listen(options.port, options.host, () => {
  console.log(`REBUILT 2D sandbox available at http://${options.host}:${options.port}/game2d/`);
});
