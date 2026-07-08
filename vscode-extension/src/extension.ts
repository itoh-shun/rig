/**
 * rig board — VS Code拡張（#286）。
 *
 * `.rig/runs/`を読み取り専用で監視し、実行中taskとgate状態をサイドバーのTree Viewに表示する。
 * 新しい状態管理エンジンは持たない——`scripts/workbench.py`が書くJSON（task.json/
 * acceptance.json/steps.json）をそのまま読むだけ。accept/discard等の書き込みコマンドは
 * 一切登録しない（読み取り専用が拡張全体の制約）。
 */
import * as path from "path";
import * as vscode from "vscode";
import { activeOnly, listTasks, TaskSummary } from "./rigState";

class TaskItem extends vscode.TreeItem {
  constructor(public readonly task: TaskSummary) {
    super(task.taskId, vscode.TreeItemCollapsibleState.None);
    this.description = `${task.status} · gate=${task.gate}`;
    this.tooltip = [
      `task: ${task.input}`,
      `type: ${task.taskType}${task.recipe ? " / recipe: " + task.recipe : ""}`,
      `mode: ${task.mode}`,
      `step: ${task.lastStep ?? "-"}`,
      `gate: ${task.gate}`,
    ].join("\n");
    this.iconPath = new vscode.ThemeIcon(iconForGate(task.gate));
    this.contextValue = "rigTask";
  }
}

function iconForGate(gate: string): string {
  switch (gate) {
    case "failed":
      return "error";
    case "pending":
      return "clock";
    case "passed_with_warnings":
      return "warning";
    case "passed":
      return "pass";
    default:
      return "circle-outline";
  }
}

class RigBoardProvider implements vscode.TreeDataProvider<TaskItem> {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private showAll = false;

  refresh(): void {
    this._onDidChangeTreeData.fire();
  }

  toggleShowAll(): void {
    this.showAll = !this.showAll;
    this.refresh();
  }

  getTreeItem(element: TaskItem): vscode.TreeItem {
    return element;
  }

  getChildren(): TaskItem[] {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) return [];
    const runsDir = path.join(folders[0].uri.fsPath, ".rig", "runs");
    let tasks = listTasks(runsDir);
    if (!this.showAll) tasks = activeOnly(tasks);
    return tasks.map((t) => new TaskItem(t));
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const provider = new RigBoardProvider();
  const view = vscode.window.createTreeView("rigBoard", {
    treeDataProvider: provider,
  });

  const folders = vscode.workspace.workspaceFolders;
  if (folders && folders.length > 0) {
    const pattern = new vscode.RelativePattern(folders[0], ".rig/runs/**");
    const watcher = vscode.workspace.createFileSystemWatcher(pattern);
    watcher.onDidChange(() => provider.refresh());
    watcher.onDidCreate(() => provider.refresh());
    watcher.onDidDelete(() => provider.refresh());
    context.subscriptions.push(watcher);
  }

  context.subscriptions.push(
    view,
    vscode.commands.registerCommand("rigBoard.refresh", () => provider.refresh()),
    vscode.commands.registerCommand("rigBoard.toggleShowAll", () => provider.toggleShowAll())
  );
}

export function deactivate(): void {
  // 監視対象の解放は context.subscriptions 経由（dispose）に任せる。
}
