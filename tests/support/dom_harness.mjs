// 最小 DOM 桩：只实现 pages/兑换管理/app.js 首屏初始化用到的接口，
// 用来在 Node 里验证「控件缺失不得阻断首屏数据加载」这一契约。
// 用法：node tests/support/dom_harness.mjs --js <app.js> [--missing all|none|id,id] [--items N]
import { readFileSync } from "node:fs";
import vm from "node:vm";

const OPTIONAL_IDS = [
  "itemContentType",
  "itemRepeatable",
  "itemSelectionMode",
  "textContentEditor",
  "mediaContentEditor",
  "contentUploadInput",
  "uploadMediaButton",
  "mediaContentList",
];

function parseArgs(argv) {
  const args = { missing: "none", items: 0 };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--js") args.js = argv[(index += 1)];
    else if (key === "--missing") args.missing = argv[(index += 1)];
    else if (key === "--items") args.items = Number(argv[(index += 1)]);
  }
  if (!args.js) throw new Error("缺少 --js 参数");
  return args;
}

function createDom(missingIds, report) {
  const nodes = new Map();
  const missingSelectors = new Set([...missingIds].map((id) => `#${id}`));

  function makeNode(selector) {
    const node = {
      selector,
      hidden: false,
      disabled: false,
      checked: false,
      value: "",
      accept: "",
      innerHTML: "",
      textContent: "",
      className: "",
      children: [],
      files: [],
      dataset: {},
      style: {},
      classList: {
        add() {},
        remove() {},
        toggle() {},
        contains() {
          return false;
        },
      },
      addEventListener(type, handler) {
        report.listenerCount += 1;
        report.listeners.push(`${selector}:${type}`);
        void handler;
      },
      removeEventListener() {},
      dispatchEvent() {
        return true;
      },
      setAttribute() {},
      removeAttribute() {},
      getAttribute() {
        return null;
      },
      hasAttribute() {
        return false;
      },
      append() {},
      appendChild() {},
      remove() {},
      focus() {},
      blur() {},
      select() {},
      click() {},
      scrollIntoView() {},
      querySelector(selector) {
        return dom.querySelector(selector);
      },
      querySelectorAll() {
        return [];
      },
      closest() {
        return null;
      },
    };
    return node;
  }

  const dom = {
    readyState: "complete",
    documentElement: makeNode("html"),
    body: makeNode("body"),
    querySelector(selector) {
      if (missingSelectors.has(selector)) return null;
      if (!nodes.has(selector)) nodes.set(selector, makeNode(selector));
      return nodes.get(selector);
    },
    querySelectorAll() {
      return [];
    },
    getElementById(id) {
      return dom.querySelector(`#${id}`);
    },
    createElement(tag) {
      return makeNode(tag);
    },
    addEventListener() {},
    removeEventListener() {},
  };
  return dom;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const source = readFileSync(args.js, "utf-8");
  const missingIds = args.missing === "none" ? [] : args.missing === "all" ? OPTIONAL_IDS : args.missing.split(",").filter(Boolean);

  const report = { listenerCount: 0, listeners: [], consoleErrors: [], apiCalls: [] };
  const dom = createDom(missingIds, report);
  const items = Array.from({ length: args.items }, (_, index) => ({
    name: `测试兑换物 ${index + 1}`,
    enabled: true,
    cost: 100,
    content_type: "text",
    contents: ["奖励内容 001", "奖励内容 002"],
    selection_mode: "sequential",
    repeatable: false,
    private_only: true,
    success_template: "{content}",
    used_count: 1,
  }));

  const payload = {
    status: "ok",
    data: {
      points_name: "积分",
      can_save: true,
      revision: 1,
      items,
      redemptions: [],
      exchange_scope: { mode: "blacklist", scope: [] },
      settings: {},
      metrics: { stock: 0, item_count: items.length, enabled_count: items.length, redeemed_count: 0 },
    },
  };

  const sandbox = {
    document: dom,
    addEventListener() {},
    removeEventListener() {},
    console: {
      log() {},
      warn() {},
      error(...params) {
        report.consoleErrors.push(params.map((param) => String(param?.message || param)).join(" "));
      },
    },
    setTimeout() {
      // 桩：不真正排程，避免测试进程因定时器不退场
      return 1;
    },
    clearTimeout() {},
    URL,
    URLSearchParams,
    Event: class Event {},
    CustomEvent: class CustomEvent {},
    AstrBotPluginPage: {
      async apiGet(endpoint) {
        report.apiCalls.push(`GET ${endpoint}`);
        return payload;
      },
      async apiPost(endpoint) {
        report.apiCalls.push(`POST ${endpoint}`);
        return payload;
      },
      async upload() {
        return { status: "ok", data: {} };
      },
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;

  const context = vm.createContext(sandbox);
  let threw = null;
  try {
    vm.runInContext(source, context, { filename: args.js });
  } catch (error) {
    threw = String(error?.stack || error);
  }
  // 让 loadData 内部的 await 链跑完
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  const output = {
    js: args.js,
    missing: missingIds,
    items: args.items,
    threw,
    apiCalls: report.apiCalls,
    overviewCalls: report.apiCalls.filter((call) => call === "GET page/overview").length,
    listenerCount: report.listenerCount,
    consoleErrors: report.consoleErrors,
    bindErrors: report.consoleErrors.filter((text) => text.includes("界面事件绑定失败")).length,
  };
  process.stdout.write(`${JSON.stringify(output)}\n`);
}

main().catch((error) => {
  process.stdout.write(`${JSON.stringify({ threw: String(error?.stack || error), apiCalls: [], overviewCalls: 0 })}\n`);
  process.exitCode = 1;
});
