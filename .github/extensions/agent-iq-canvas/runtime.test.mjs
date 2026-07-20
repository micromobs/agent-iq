import assert from "node:assert/strict";
import http from "node:http";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { assertProjectionSafe, CanvasRuntime } from "./runtime.mjs";

function request({ port, path, method = "GET", headers = {}, body = null }) {
  return new Promise((resolve, reject) => {
    const outgoing = http.request(
      {
        hostname: "127.0.0.1",
        port,
        path,
        method,
        headers,
      },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () =>
          resolve({
            status: response.statusCode,
            headers: response.headers,
            body: Buffer.concat(chunks).toString("utf8"),
          }),
        );
      },
    );
    outgoing.on("error", reject);
    if (body !== null) outgoing.write(body);
    outgoing.end();
  });
}

test("loopback runtime enforces host, token, origin, and path boundaries", async () => {
  const runtime = new CanvasRuntime({
    webRoot: fileURLToPath(new URL("./web", import.meta.url)),
    demoPath: fileURLToPath(
      new URL("./web/demo/launch-command-center.agui.json", import.meta.url),
    ),
  });
  const port = await runtime.start();
  try {
    await runtime.openInstance("instance-test");
    const canvasUrl = new URL(runtime.url("instance-test"));

    const misdirected = await request({
      port,
      path: `${canvasUrl.pathname}${canvasUrl.search}`,
      headers: { Host: "attacker.example" },
    });
    assert.equal(misdirected.status, 421);

    const denied = await request({
      port,
      path: "/api/state?instanceId=instance-test&token=wrong",
      headers: { Host: `127.0.0.1:${port}` },
    });
    assert.equal(denied.status, 403);

    const root = await request({
      port,
      path: `${canvasUrl.pathname}${canvasUrl.search}`,
      headers: { Host: `127.0.0.1:${port}` },
    });
    assert.equal(root.status, 200);
    assert.match(root.headers["content-security-policy"], /default-src 'none'/);
    assert.match(root.headers["set-cookie"][0], /HttpOnly; SameSite=Strict/);

    const state = await request({
      port,
      path: `/api/state?instanceId=instance-test&token=${runtime.token}`,
      headers: { Host: `127.0.0.1:${port}` },
    });
    assert.equal(state.status, 200);
    assert.equal(JSON.parse(state.body).projection.executionId, "demo-eu-launch-command-center");

    const assetPath = root.body.match(/\.\/(assets\/[^"]+\.js)/)?.[1];
    assert.ok(assetPath);
    const asset = await request({
      port,
      path: `/${assetPath}`,
      headers: { Host: `127.0.0.1:${port}` },
    });
    assert.equal(asset.status, 200);
    assert.match(asset.headers["content-type"], /text\/javascript/);

    const cursorBody = JSON.stringify({ instanceId: "instance-test", cursor: 2 });
    const crossOrigin = await request({
      port,
      path: "/api/cursor",
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Origin: "https://attacker.example",
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(cursorBody),
        "X-Agent-IQ-Token": runtime.token,
      },
      body: cursorBody,
    });
    assert.equal(crossOrigin.status, 403);

    const cursor = await request({
      port,
      path: "/api/cursor",
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Origin: `http://127.0.0.1:${port}`,
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(cursorBody),
        "X-Agent-IQ-Token": runtime.token,
      },
      body: cursorBody,
    });
    assert.equal(cursor.status, 200);
    assert.equal(runtime.snapshot("instance-test").cursor, 2);

    const traversal = await request({
      port,
      path: `/%2e%2e%2fpackage.json?token=${runtime.token}`,
      headers: { Host: `127.0.0.1:${port}` },
    });
    assert.equal(traversal.status, 403);
  } finally {
    await runtime.stop();
  }
});

test("projection boundary rejects restricted fields before browser delivery", () => {
  assert.throws(
    () =>
      assertProjectionSafe({
        executionId: "execution-1",
        threadId: "thread-1",
        eventCount: 1,
        events: [
          {
            executionId: "execution-1",
            event: { type: "RUN_STARTED" },
          },
        ],
        tenantId: "must-not-cross",
      }),
    /Restricted projection field/,
  );
});
