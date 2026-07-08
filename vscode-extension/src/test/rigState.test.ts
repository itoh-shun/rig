/**
 * rigState.tsのユニットテスト（#286）。vscodeモジュールに依存しないため、
 * VS Code Extension Hostなしでnodeだけで実行できる：`npm run compile && npm run test:unit`
 */
import * as assert from "assert";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { activeOnly, gateStatusFromChecks, listTasks } from "../rigState";

function writeTask(runsDir: string, taskId: string, task: object, acceptance?: object, steps?: object): void {
  const dir = path.join(runsDir, taskId);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "task.json"), JSON.stringify({ task_id: taskId, ...task }));
  if (acceptance) fs.writeFileSync(path.join(dir, "acceptance.json"), JSON.stringify(acceptance));
  if (steps) fs.writeFileSync(path.join(dir, "steps.json"), JSON.stringify(steps));
}

function main(): void {
  // gateStatusFromChecks: workbench.py gate_status() と同じ優先順位
  assert.strictEqual(gateStatusFromChecks([]), "skipped");
  assert.strictEqual(gateStatusFromChecks([{ status: "passed" }, { status: "failed" }]), "failed");
  assert.strictEqual(gateStatusFromChecks([{ status: "passed" }, { status: "pending" }]), "pending");
  assert.strictEqual(gateStatusFromChecks([{ status: "skipped" }, { status: "skipped" }]), "skipped");
  assert.strictEqual(gateStatusFromChecks([{ status: "passed" }, { status: "warning" }]), "passed_with_warnings");
  assert.strictEqual(gateStatusFromChecks([{ status: "passed" }, { status: "passed" }]), "passed");
  console.log("[OK] gateStatusFromChecks matches workbench.py gate_status() priority order");

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rig-vscode-test-"));
  const runsDir = path.join(tmp, ".rig", "runs");

  // 存在しない runsDir → 空配列（クラッシュしない）
  assert.deepStrictEqual(listTasks(runsDir), []);
  console.log("[OK] listTasks returns [] for missing runs dir");

  writeTask(
    runsDir,
    "rig-a",
    { input: "fix login bug", task_type: "bugfix", status: "running", worktree_path: "/tmp/wt-a" },
    { checks: [{ name: "no_secret_leak", status: "pending" }] },
    { steps: [{ name: "implement", status: "done" }] }
  );
  writeTask(
    runsDir,
    "rig-b",
    { input: "docs only", task_type: "documentation", status: "accepted" },
    { checks: [{ name: "task_intent_satisfied", status: "passed" }] }
  );

  const tasks = listTasks(runsDir);
  assert.strictEqual(tasks.length, 2);
  const a = tasks.find((t) => t.taskId === "rig-a")!;
  assert.strictEqual(a.mode, "isolated");
  assert.strictEqual(a.gate, "pending");
  assert.strictEqual(a.lastStep, "implement(done)");
  const b = tasks.find((t) => t.taskId === "rig-b")!;
  assert.strictEqual(b.mode, "not-isolated");
  assert.strictEqual(b.gate, "passed");
  console.log("[OK] listTasks parses task.json/acceptance.json/steps.json correctly");

  const active = activeOnly(tasks);
  assert.deepStrictEqual(
    active.map((t) => t.taskId),
    ["rig-a"]
  );
  console.log("[OK] activeOnly filters out accepted/discarded (matches workbench.py board default)");

  fs.rmSync(tmp, { recursive: true, force: true });
  console.log("\nALL OK");
}

main();
