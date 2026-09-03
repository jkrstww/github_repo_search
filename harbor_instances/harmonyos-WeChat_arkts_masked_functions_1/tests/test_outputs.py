import json
import subprocess
from pathlib import Path


def test_ability_registration_runtime():
    source_path = Path(__file__).resolve().parents[1] / "entry/src/ohosTest/ets/test/Ability.test.ets"
    source = source_path.read_text(encoding="utf-8")
    payload = json.dumps(source)
    script = r"""
const vm = require("vm");
const source = JSON.parse(process.argv[1])
  .replace(/^import[^\n]*\n/gm, "")
  .replace("export default function", "function");
const events = { suites: [], hooks: [], cases: [], logs: [], assertions: [] };
const context = {
  describe(name, callback) {
    const suite = { name, hooks: [], cases: [] };
    events.suites.push(suite);
    callback();
  },
  beforeAll(callback) { events.hooks.push("beforeAll"); callback(); },
  beforeEach(callback) { events.hooks.push("beforeEach"); callback(); },
  afterEach(callback) { events.hooks.push("afterEach"); callback(); },
  afterAll(callback) { events.hooks.push("afterAll"); callback(); },
  it(name, filter, callback) {
    events.cases.push({ name, filter });
    callback();
  },
  hilog: { info(...args) { events.logs.push(args); } },
  expect(value) {
    return {
      assertContain(other) {
        if (!String(value).includes(String(other))) throw new Error("assertContain failed");
        events.assertions.push("contain");
      },
      assertEqual(other) {
        if (value !== other) throw new Error("assertEqual failed");
        events.assertions.push("equal");
      },
    };
  },
  module: { exports: {} },
};
vm.runInNewContext(source + "\nmodule.exports = abilityTest;", context);
if (typeof context.module.exports !== "function") throw new Error("abilityTest was not exported");
context.module.exports();
if (events.suites.length !== 1 || events.suites[0].name !== "ActsAbilityTest") throw new Error("suite registration is incomplete");
if (JSON.stringify(events.hooks) !== JSON.stringify(["beforeAll", "beforeEach", "afterEach", "afterAll"])) throw new Error("lifecycle hooks are incomplete");
if (events.cases.length !== 1 || events.cases[0].name !== "assertContain" || events.cases[0].filter !== 0) throw new Error("assertion case is incomplete");
if (events.logs.length !== 1 || events.logs[0][0] !== 0x0000 || events.logs[0][1] !== "testTag") throw new Error("test logging is incomplete");
if (JSON.stringify(events.assertions) !== JSON.stringify(["contain", "equal"])) throw new Error("assertions were not executed");
"""
    result = subprocess.run(["node", "-e", script, payload], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
