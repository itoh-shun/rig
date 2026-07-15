/**
 * Pure functions that read-only-parse `.rig/runs/` (#286).
 *
 * No dependency on the `vscode` module — this lets it be unit-tested with plain
 * Node, so the parsing logic can be verified correct even in an environment
 * without a VS Code Extension Host. There is no write path here, or anywhere
 * else in the extension (accept/discard equivalents don't exist).
 */
import * as fs from "fs";
import * as path from "path";

export interface TaskSummary {
  taskId: string;
  input: string;
  taskType: string;
  recipe: string | null;
  status: string;
  mode: "isolated" | "not-isolated";
  gate: string; // "-" | "passed" | "passed_with_warnings" | "failed" | "pending" | "skipped"
  lastStep: string | null;
}

interface TaskJson {
  task_id: string;
  input: string;
  task_type: string;
  recipe?: string;
  status: string;
  worktree_path?: string;
}

interface AcceptanceJson {
  status?: string;
  checks?: Array<{ name: string; status: string }>;
}

interface StepsJson {
  steps?: Array<{ name: string; status: string }>;
}

function readJsonSafe<T>(filePath: string): T | null {
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/**
 * Derives gate status from `acceptance.json`'s checks, following the same
 * priority order as workbench.py's `gate_status()` (failed > pending >
 * all-skipped > warning > passed) — the ported logic must never diverge from
 * the one place that priority order is actually defined.
 */
export function gateStatusFromChecks(checks: Array<{ status: string }>): string {
  if (!checks.length) return "skipped";
  const statuses = checks.map((c) => c.status);
  if (statuses.some((s) => s === "failed")) return "failed";
  if (statuses.some((s) => s === "pending")) return "pending";
  if (statuses.every((s) => s === "skipped")) return "skipped";
  if (statuses.some((s) => s === "warning")) return "passed_with_warnings";
  return "passed";
}

/** Reads every task.json under `runsDir` (`<repo>/.rig/runs`) and returns a TaskSummary array. */
export function listTasks(runsDir: string): TaskSummary[] {
  if (!fs.existsSync(runsDir) || !fs.statSync(runsDir).isDirectory()) {
    return [];
  }
  const out: TaskSummary[] = [];
  for (const entry of fs.readdirSync(runsDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const dir = path.join(runsDir, entry.name);
    const task = readJsonSafe<TaskJson>(path.join(dir, "task.json"));
    if (!task) continue;
    const acc = readJsonSafe<AcceptanceJson>(path.join(dir, "acceptance.json"));
    const steps = readJsonSafe<StepsJson>(path.join(dir, "steps.json"));
    const checks = acc?.checks ?? [];
    const lastStep =
      steps?.steps && steps.steps.length > 0
        ? `${steps.steps[steps.steps.length - 1].name}(${steps.steps[steps.steps.length - 1].status})`
        : null;
    out.push({
      taskId: task.task_id,
      input: task.input,
      taskType: task.task_type,
      recipe: task.recipe ?? null,
      status: task.status,
      mode: task.worktree_path ? "isolated" : "not-isolated",
      gate: checks.length ? gateStatusFromChecks(checks) : "-",
      lastStep,
    });
  }
  out.sort((a, b) => a.taskId.localeCompare(b.taskId));
  return out;
}

const ACTIVE_STATUSES = new Set(["running", "gate_failed", "gate_passed"]);

export function activeOnly(tasks: TaskSummary[]): TaskSummary[] {
  return tasks.filter((t) => ACTIVE_STATUSES.has(t.status));
}
