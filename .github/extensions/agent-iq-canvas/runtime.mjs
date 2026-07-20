import { randomBytes, timingSafeEqual } from "node:crypto";
import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import http from "node:http";
import { extname, resolve, sep } from "node:path";

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const MAX_PROJECTION_BYTES = 4 * 1024 * 1024;
const MAX_REQUEST_BYTES = 4096;
const COOKIE_NAME = "agent_iq_canvas";
const RESTRICTED_KEYS = new Set([
  "requestid",
  "intent",
  "query",
  "subjectid",
  "tenantid",
  "contextid",
  "sourceuri",
  "toolcallargs",
  "arguments",
  "providererror",
  "reasoning",
  "chainofthought",
  "rawevent",
  "prompt",
  "credential",
  "accesstoken",
]);

const CONTENT_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
};

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function safeEqual(left, right) {
  const leftBytes = Buffer.from(left);
  const rightBytes = Buffer.from(right);
  return leftBytes.length === rightBytes.length && timingSafeEqual(leftBytes, rightBytes);
}

function parseCookies(header = "") {
  const cookies = new Map();
  for (const pair of header.split(";")) {
    const separator = pair.indexOf("=");
    if (separator <= 0) continue;
    cookies.set(pair.slice(0, separator).trim(), pair.slice(separator + 1).trim());
  }
  return cookies;
}

export function assertIdentifier(value, label) {
  if (typeof value !== "string" || !IDENTIFIER.test(value)) {
    throw new Error(`${label} must be a bounded Agent IQ identifier.`);
  }
  return value;
}

function scanRestricted(value, path = "$") {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => scanRestricted(entry, `${path}[${index}]`));
    return;
  }
  if (!isRecord(value)) return;
  for (const [key, entry] of Object.entries(value)) {
    const normalized = key.replaceAll("_", "").toLowerCase();
    if (RESTRICTED_KEYS.has(normalized)) {
      throw new Error(`Restricted projection field ${path}.${key}.`);
    }
    scanRestricted(entry, `${path}.${key}`);
  }
}

export function assertProjectionSafe(value) {
  scanRestricted(value);
  if (
    !isRecord(value) ||
    typeof value.executionId !== "string" ||
    typeof value.threadId !== "string" ||
    !Array.isArray(value.events) ||
    typeof value.eventCount !== "number" ||
    value.eventCount !== value.events.length ||
    value.events.length === 0
  ) {
    throw new Error("Agent IQ service returned an invalid AG-UI projection.");
  }
  if (value.events[0]?.event?.type !== "RUN_STARTED") {
    throw new Error("Agent IQ projection does not begin with RUN_STARTED.");
  }
  for (const envelope of value.events) {
    if (envelope?.executionId !== value.executionId) {
      throw new Error("Agent IQ projection mixes execution identifiers.");
    }
  }
  return value;
}

function isTerminal(projection) {
  const type = projection.events.at(-1)?.event?.type;
  return type === "RUN_FINISHED" || type === "RUN_ERROR";
}

function validateServiceBaseUrl(value) {
  if (!value) return null;
  const parsed = new URL(value);
  const loopback = parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost";
  if (parsed.protocol !== "https:" && !(loopback && parsed.protocol === "http:")) {
    throw new Error("AGENT_IQ_HTTP_URL must use HTTPS or a loopback HTTP origin.");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("AGENT_IQ_HTTP_URL cannot contain credentials, query, or fragment data.");
  }
  return parsed;
}

function securityHeaders() {
  return {
    "Content-Security-Policy": [
      "default-src 'none'",
      "script-src 'self'",
      "style-src 'self'",
      "connect-src 'self'",
      "img-src 'self' data:",
      "font-src 'self'",
      "base-uri 'none'",
      "object-src 'none'",
      "form-action 'none'",
    ].join("; "),
    "Permissions-Policy":
      "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  };
}

function sendJson(response, statusCode, payload, headers = {}) {
  const body = JSON.stringify(payload);
  response.writeHead(statusCode, {
    ...securityHeaders(),
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    ...headers,
  });
  response.end(body);
}

function sendText(response, statusCode, text) {
  response.writeHead(statusCode, {
    ...securityHeaders(),
    "Cache-Control": "no-store",
    "Content-Type": "text/plain; charset=utf-8",
    "Content-Length": Buffer.byteLength(text),
  });
  response.end(text);
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_REQUEST_BYTES) {
      throw new Error("Canvas request body is too large.");
    }
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

export class CanvasRuntime {
  constructor({
    webRoot,
    demoPath,
    serviceBaseUrl = process.env.AGENT_IQ_HTTP_URL,
    serviceToken = process.env.AGENT_IQ_API_TOKEN,
    fetchImpl = fetch,
    pollIntervalMs = 1200,
  }) {
    this.webRoot = resolve(webRoot);
    this.demoPath = resolve(demoPath);
    this.serviceBaseUrl = validateServiceBaseUrl(serviceBaseUrl);
    this.serviceToken = serviceToken || null;
    this.fetchImpl = fetchImpl;
    this.pollIntervalMs = pollIntervalMs;
    this.token = randomBytes(32).toString("base64url");
    this.instances = new Map();
    this.streams = new Map();
    this.server = null;
    this.port = null;
  }

  async start() {
    if (!existsSync(resolve(this.webRoot, "index.html"))) {
      throw new Error("Agent IQ Canvas web build is missing. Run npm run build in apps/canvas.");
    }
    const demo = JSON.parse(readFileSync(this.demoPath, "utf8"));
    assertProjectionSafe(demo);
    this.demo = demo;
    this.server = http.createServer((request, response) => {
      void this._handle(request, response).catch(() => {
        if (!response.headersSent) sendText(response, 500, "Canvas request failed.");
        else response.destroy();
      });
    });
    await new Promise((resolveStart, rejectStart) => {
      this.server.once("error", rejectStart);
      this.server.listen(0, "127.0.0.1", () => {
        this.server.off("error", rejectStart);
        const address = this.server.address();
        this.port = typeof address === "object" && address !== null ? address.port : null;
        resolveStart();
      });
    });
    if (this.port === null) throw new Error("Agent IQ Canvas did not bind a loopback port.");
    return this.port;
  }

  async stop() {
    for (const instanceId of [...this.instances.keys()]) this.closeInstance(instanceId);
    if (this.server === null) return;
    await new Promise((resolveStop) => this.server.close(() => resolveStop()));
    this.server = null;
    this.port = null;
  }

  async openInstance(instanceId, input = {}) {
    assertIdentifier(instanceId, "Canvas instance ID");
    this.closeInstance(instanceId);
    this.instances.set(instanceId, {
      projection: cloneJson(this.demo),
      cursor: this.demo.events.length - 1,
      mode: "demo",
      source: "Bundled synthetic certification replay",
      status: "ready",
      poll: null,
      executionId: null,
      threadId: null,
    });
    if (isRecord(input) && typeof input.executionId === "string") {
      await this.loadExecution(
        instanceId,
        input.executionId,
        typeof input.threadId === "string" ? input.threadId : `canvas-${instanceId}`,
      );
    }
    return this.snapshot(instanceId);
  }

  closeInstance(instanceId) {
    const instance = this.instances.get(instanceId);
    if (instance?.poll) clearInterval(instance.poll);
    this.instances.delete(instanceId);
    for (const response of this.streams.get(instanceId) ?? []) response.end();
    this.streams.delete(instanceId);
  }

  loadDemo(instanceId) {
    const instance = this._instance(instanceId);
    if (instance.poll) clearInterval(instance.poll);
    Object.assign(instance, {
      projection: cloneJson(this.demo),
      cursor: this.demo.events.length - 1,
      mode: "demo",
      source: "Bundled synthetic certification replay",
      status: "ready",
      poll: null,
      executionId: null,
      threadId: null,
    });
    this._broadcast(instanceId);
    return this.snapshot(instanceId);
  }

  async loadExecution(instanceId, executionId, threadId) {
    assertIdentifier(executionId, "Execution ID");
    assertIdentifier(threadId, "Thread ID");
    if (this.serviceBaseUrl === null) {
      throw new Error("AGENT_IQ_HTTP_URL is required to load an authorized live execution.");
    }
    const projection = await this._fetchProjection(executionId, threadId);
    const instance = this._instance(instanceId);
    if (instance.poll) clearInterval(instance.poll);
    Object.assign(instance, {
      projection,
      cursor: projection.events.length - 1,
      mode: "live",
      source: "Authorized Agent IQ projection",
      status: isTerminal(projection) ? "complete" : "following",
      executionId,
      threadId,
      poll: null,
    });
    this._broadcast(instanceId);
    if (!isTerminal(projection)) {
      instance.poll = setInterval(() => {
        void this._refresh(instanceId);
      }, this.pollIntervalMs);
    }
    return this.snapshot(instanceId);
  }

  setCursor(instanceId, cursor) {
    const instance = this._instance(instanceId);
    if (!Number.isInteger(cursor) || cursor < 0 || cursor >= instance.projection.events.length) {
      throw new Error("Replay cursor is outside the projected event range.");
    }
    instance.cursor = cursor;
    this._broadcast(instanceId);
    return this.snapshot(instanceId);
  }

  snapshot(instanceId) {
    const instance = this._instance(instanceId);
    return {
      projection: instance.projection,
      cursor: instance.cursor,
      mode: instance.mode,
      source: instance.source,
      status: instance.status,
    };
  }

  url(instanceId) {
    if (this.port === null) throw new Error("Canvas runtime has not started.");
    assertIdentifier(instanceId, "Canvas instance ID");
    const parameters = new URLSearchParams({ instanceId, token: this.token });
    return `http://127.0.0.1:${this.port}/?${parameters.toString()}`;
  }

  async _fetchProjection(executionId, threadId) {
    const endpoint = new URL(
      `/v1/executions/${encodeURIComponent(executionId)}/ag-ui`,
      this.serviceBaseUrl,
    );
    endpoint.searchParams.set("thread_id", threadId);
    const headers = { Accept: "application/json" };
    if (this.serviceToken !== null) headers.Authorization = `Bearer ${this.serviceToken}`;
    const response = await this.fetchImpl(endpoint, {
      method: "GET",
      headers,
      redirect: "error",
    });
    if (!response.ok) throw new Error("Agent IQ projection request was not authorized.");
    const declaredLength = Number(response.headers.get("content-length") ?? "0");
    if (declaredLength > MAX_PROJECTION_BYTES) {
      throw new Error("Agent IQ projection exceeds the Canvas size limit.");
    }
    const text = await response.text();
    if (Buffer.byteLength(text) > MAX_PROJECTION_BYTES) {
      throw new Error("Agent IQ projection exceeds the Canvas size limit.");
    }
    return assertProjectionSafe(JSON.parse(text));
  }

  async _refresh(instanceId) {
    const instance = this.instances.get(instanceId);
    if (!instance?.executionId || !instance.threadId) return;
    try {
      const projection = await this._fetchProjection(instance.executionId, instance.threadId);
      instance.projection = projection;
      instance.cursor = projection.events.length - 1;
      instance.status = isTerminal(projection) ? "complete" : "following";
      if (isTerminal(projection) && instance.poll) {
        clearInterval(instance.poll);
        instance.poll = null;
      }
      this._broadcast(instanceId);
    } catch {
      instance.status = "live update unavailable";
      this._broadcast(instanceId);
    }
  }

  _instance(instanceId) {
    assertIdentifier(instanceId, "Canvas instance ID");
    const instance = this.instances.get(instanceId);
    if (!instance) throw new Error("Canvas instance is not open.");
    return instance;
  }

  _authorized(request, url) {
    const queryToken = url.searchParams.get("token");
    const headerToken = request.headers["x-agent-iq-token"];
    const cookieToken = parseCookies(request.headers.cookie).get(COOKIE_NAME);
    return [queryToken, headerToken, cookieToken].some(
      (value) => typeof value === "string" && safeEqual(value, this.token),
    );
  }

  _validHost(request) {
    return this.port !== null && request.headers.host === `127.0.0.1:${this.port}`;
  }

  _validOrigin(request) {
    return (
      this.port !== null &&
      request.headers.origin === `http://127.0.0.1:${this.port}` &&
      (!request.headers["sec-fetch-site"] || request.headers["sec-fetch-site"] === "same-origin")
    );
  }

  async _handle(request, response) {
    if (!this._validHost(request)) {
      sendText(response, 421, "Misdirected request.");
      return;
    }
    const url = new URL(request.url ?? "/", `http://127.0.0.1:${this.port}`);

    if (request.method === "GET" && url.pathname === "/") {
      if (!this._authorized(request, url)) {
        sendText(response, 403, "Canvas authorization failed.");
        return;
      }
      this._instance(url.searchParams.get("instanceId"));
      this._serveFile(response, "index.html", {
        "Set-Cookie": `${COOKIE_NAME}=${this.token}; HttpOnly; SameSite=Strict; Path=/`,
      });
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/state") {
      if (!this._authorized(request, url)) {
        sendText(response, 403, "Canvas authorization failed.");
        return;
      }
      sendJson(response, 200, this.snapshot(url.searchParams.get("instanceId")));
      return;
    }

    if (request.method === "GET" && url.pathname === "/api/events") {
      if (!this._authorized(request, url)) {
        sendText(response, 403, "Canvas authorization failed.");
        return;
      }
      const instanceId = assertIdentifier(
        url.searchParams.get("instanceId"),
        "Canvas instance ID",
      );
      this._instance(instanceId);
      response.writeHead(200, {
        ...securityHeaders(),
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "Content-Type": "text/event-stream; charset=utf-8",
        "X-Accel-Buffering": "no",
      });
      response.write(`event: state\ndata: ${JSON.stringify(this.snapshot(instanceId))}\n\n`);
      const clients = this.streams.get(instanceId) ?? new Set();
      clients.add(response);
      this.streams.set(instanceId, clients);
      const heartbeat = setInterval(() => response.write(": keepalive\n\n"), 15_000);
      request.on("close", () => {
        clearInterval(heartbeat);
        clients.delete(response);
      });
      return;
    }

    if (request.method === "POST" && url.pathname === "/api/cursor") {
      if (!this._authorized(request, url) || !this._validOrigin(request)) {
        sendText(response, 403, "Canvas authorization failed.");
        return;
      }
      if (!request.headers["content-type"]?.startsWith("application/json")) {
        sendText(response, 415, "Canvas requires JSON.");
        return;
      }
      const body = await readJsonBody(request);
      if (!isRecord(body)) throw new Error("Canvas cursor request must be an object.");
      this.setCursor(body.instanceId, body.cursor);
      sendJson(response, 200, { ok: true });
      return;
    }

    if ((request.method === "GET" || request.method === "HEAD") && url.pathname !== "/") {
      const publicAsset = url.pathname.startsWith("/assets/");
      if (!publicAsset && !this._authorized(request, url)) {
        sendText(response, 403, "Canvas authorization failed.");
        return;
      }
      const requestedPath = decodeURIComponent(url.pathname.slice(1));
      this._serveFile(response, requestedPath, {}, request.method === "HEAD");
      return;
    }

    sendText(response, 404, "Not found.");
  }

  _serveFile(response, requestedPath, extraHeaders = {}, headOnly = false) {
    if (requestedPath.includes("\0")) {
      sendText(response, 400, "Invalid path.");
      return;
    }
    const path = resolve(this.webRoot, requestedPath);
    if (path !== this.webRoot && !path.startsWith(`${this.webRoot}${sep}`)) {
      sendText(response, 403, "Path is outside the Canvas web root.");
      return;
    }
    if (!existsSync(path) || !statSync(path).isFile()) {
      sendText(response, 404, "Not found.");
      return;
    }
    const extension = extname(path).toLowerCase();
    const size = statSync(path).size;
    response.writeHead(200, {
      ...securityHeaders(),
      "Cache-Control": requestedPath.startsWith("assets/")
        ? "public, max-age=31536000, immutable"
        : "no-store",
      "Content-Type": CONTENT_TYPES[extension] ?? "application/octet-stream",
      "Content-Length": size,
      ...extraHeaders,
    });
    if (headOnly) response.end();
    else createReadStream(path).pipe(response);
  }

  _broadcast(instanceId) {
    const payload = `event: state\ndata: ${JSON.stringify(this.snapshot(instanceId))}\n\n`;
    for (const response of this.streams.get(instanceId) ?? []) response.write(payload);
  }
}
