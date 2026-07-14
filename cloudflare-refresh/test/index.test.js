import assert from "node:assert/strict";
import test from "node:test";

import worker, { dispatchWorkflow } from "../src/index.js";


const env = {
  GITHUB_ACTIONS_TOKEN: "test-token",
  GITHUB_OWNER: "L1997x",
  GITHUB_REPO: "A-share-LI",
  GITHUB_WORKFLOW: "update-data.yml",
  GITHUB_REF: "main",
};


test("dispatches the workflow with the Cloudflare cron", async () => {
  let request;
  const result = await dispatchWorkflow(env, "53 1 * * 1-5", async (url, options) => {
    request = { url, options };
    return new Response(null, { status: 204 });
  });

  assert.equal(result.status, 204);
  assert.equal(request.url, "https://api.github.com/repos/L1997x/A-share-LI/actions/workflows/update-data.yml/dispatches");
  assert.equal(request.options.headers.Authorization, "Bearer test-token");
  assert.deepEqual(JSON.parse(request.options.body), {
    ref: "main",
    inputs: {
      trigger_source: "cloudflare",
      target_schedule: "53 1 * * 1-5",
    },
  });
});


test("fails closed when the GitHub token is missing", async () => {
  await assert.rejects(() => dispatchWorkflow({}, "53 1 * * 1-5", async () => new Response()), /not configured/);
});


test("scheduled handler registers the dispatch promise", async () => {
  let scheduledPromise;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(null, { status: 204 });
  try {
    worker.scheduled(
      { cron: "13 3 * * 1-5" },
      env,
      { waitUntil: (promise) => { scheduledPromise = promise; } }
    );
    assert.ok(scheduledPromise instanceof Promise);
    await scheduledPromise;
  } finally {
    globalThis.fetch = originalFetch;
  }
});
