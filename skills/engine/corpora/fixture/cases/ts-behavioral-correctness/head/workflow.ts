export type SubmissionState = {
  previewing: boolean;
  confirming: boolean;
  lines: number;
  shortfalls: number;
};

export function dialogSubmitting(state: SubmissionState): boolean {
  return state.confirming;
}

export function canLeaveWithoutConfirmation(state: SubmissionState): boolean {
  return state.lines === 0;
}

export type QuantityItem = {
  mode: "ORDER" | "DISPATCH";
  recommendedQuantity: number;
  inventoryUnit: string;
  orderUnit: string | null;
};

export function recommendedQuantityLabel(item: QuantityItem): string {
  return `${item.recommendedQuantity} ${item.inventoryUnit}`;
}

export type ConsumptionEvent = { date: string; quantity: number };

export function dailySigma(events: ConsumptionEvent[]): number {
  if (events.length === 0) return 0;
  const total = events.reduce((sum, event) => sum + event.quantity, 0);
  const mean = total / events.length;
  const sumSquares = events.reduce((sum, event) => sum + event.quantity ** 2, 0);
  const variance = Math.max(0, sumSquares / events.length - mean ** 2);
  return Math.sqrt(variance);
}

export type SortKey = "product" | "department" | "demand";
export type SortOrder = "ASC" | "DESC";
export type SortSelection = { key: SortKey; order: SortOrder };

export const mobileSortOptions: Array<{ value: SortKey; label: string }> = [
  { value: "product", label: "商品名" },
  { value: "department", label: "部署" },
  { value: "demand", label: "需要" },
];

export function applyMobileSortSelection(
  current: SortSelection,
  selectedKey: SortKey,
): SortSelection {
  if (current.key === selectedKey) {
    return { key: selectedKey, order: current.order === "ASC" ? "DESC" : "ASC" };
  }
  return { key: selectedKey, order: "ASC" };
}
