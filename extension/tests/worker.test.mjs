import test from "node:test";
import assert from "node:assert/strict";

const storage = {};
const openedTabs = [];
let messageListener;

globalThis.chrome = {
  runtime: {
    onInstalled: { addListener() {} },
    onStartup: { addListener() {} },
    onMessage: {
      addListener(listener) {
        messageListener = listener;
      },
    },
  },
  sidePanel: { async setPanelBehavior() {} },
  storage: {
    local: {
      async get(key) {
        return { [key]: storage[key] };
      },
      async set(values) {
        Object.assign(storage, values);
      },
      async remove(key) {
        delete storage[key];
      },
    },
  },
  permissions: {
    async contains() {
      return true;
    },
    async remove() {
      return true;
    },
  },
  tabs: {
    async create(options) {
      openedTabs.push(options);
    },
    async query() {
      return [];
    },
  },
  scripting: { async executeScript() {} },
};

await import(`../service-worker.js?worker-test=${Date.now()}`);

function send(message) {
  return new Promise((resolve) => {
    const keepAlive = messageListener(message, {}, resolve);
    assert.equal(keepAlive, true);
  });
}

function json(status, value) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("exchanges one-time pairing credentials and saves only a dashboard-verified bearer", async () => {
  delete storage.recruitingEngineCompanion;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    if (url.endsWith("/api/v1/health")) {
      return json(200, { status: "ok", version: "0.1.0", auth_required: true });
    }
    if (url.endsWith("/api/v1/pair")) {
      return json(200, { bearer_token: "re_local_verified", token_type: "Bearer" });
    }
    if (url.endsWith("/api/v1/dashboard")) {
      return json(200, { snapshot: { counts: {} } });
    }
    return json(404, { error: { message: "missing" } });
  };

  const response = await send({
    type: "PAIR_COMPANION",
    payload: { baseUrl: "http://127.0.0.1:8765", token: "re_pair_once" },
  });

  assert.equal(response.ok, true);
  assert.equal(response.data.dashboardVerified, true);
  assert.equal(storage.recruitingEngineCompanion.token, "re_local_verified");
  assert.equal(JSON.parse(requests[1].init.body).pairing_token, "re_pair_once");
  assert.equal(requests[0].init.headers.Authorization, undefined);
  assert.equal(requests[1].init.headers.Authorization, undefined);
  assert.equal(requests[2].init.headers.Authorization, "Bearer re_local_verified");
});

test("does not save an unverified bearer", async () => {
  delete storage.recruitingEngineCompanion;
  globalThis.fetch = async (url) => {
    if (url.endsWith("/api/v1/health")) {
      return json(200, { status: "ok", auth_required: true });
    }
    if (url.endsWith("/api/v1/dashboard")) {
      return json(401, { error: { code: "unauthorized", message: "Invalid bearer" } });
    }
    return json(404, { error: { message: "missing" } });
  };

  const response = await send({
    type: "PAIR_COMPANION",
    payload: { baseUrl: "http://localhost:8765", token: "re_local_bad" },
  });
  assert.equal(response.ok, false);
  assert.match(response.error, /Invalid bearer/);
  assert.equal(storage.recruitingEngineCompanion, undefined);
});

test("opens only the fixed hosted product URL without reading companion configuration", async () => {
  delete storage.recruitingEngineCompanion;
  openedTabs.length = 0;
  const response = await send({ type: "OPEN_APP_PATH", payload: { path: "/app/outreach" } });
  assert.equal(response.ok, true);
  assert.deepEqual(openedTabs, [{ url: "https://axe-pat.github.io/app/outreach" }]);

  const rejected = await send({ type: "OPEN_APP_PATH", payload: { path: "https://evil.example" } });
  assert.equal(rejected.ok, false);
  assert.equal(openedTabs.length, 1);
});
