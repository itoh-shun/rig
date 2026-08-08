export type SubmissionState = {
  previewing: boolean;
  confirming: boolean;
  lines: number;
  shortfalls: number;
};

export function dialogSubmitting(state: SubmissionState): boolean {
  return state.previewing || state.confirming;
}

export function canLeaveWithoutConfirmation(state: SubmissionState): boolean {
  return state.lines === 0 && state.shortfalls === 0;
}

export type QuantityItem = {
  mode: "ORDER" | "DISPATCH";
  recommendedQuantity: number;
  inventoryUnit: string;
  orderUnit: string | null;
};

export function recommendedQuantityLabel(item: QuantityItem): string {
  const unit = item.mode === "ORDER" ? item.orderUnit ?? item.inventoryUnit : item.inventoryUnit;
  return `${item.recommendedQuantity} ${unit}`;
}

export type ConsumptionEvent = { date: string; quantity: number };

export function dailySigma(events: ConsumptionEvent[]): number {
  const daily = new Map<string, number>();
  for (const event of events) {
    daily.set(event.date, (daily.get(event.date) ?? 0) + event.quantity);
  }
  const values = [...daily.values()];
  if (values.length === 0) return 0;
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length;
  return Math.sqrt(variance);
}

export type SortKey = "product" | "department" | "demand";
export type SortOrder = "ASC" | "DESC";
export type SortSelection = { key: SortKey; order: SortOrder };

export const mobileSortOptions: Array<{ value: string; label: string }> = [
  { value: "product:ASC", label: "商品名 ↑" },
  { value: "product:DESC", label: "商品名 ↓" },
  { value: "department:ASC", label: "部署 ↑" },
  { value: "department:DESC", label: "部署 ↓" },
  { value: "demand:ASC", label: "需要 ↑" },
  { value: "demand:DESC", label: "需要 ↓" },
];

export function applyMobileSortSelection(value: string): SortSelection {
  const [key, order] = value.split(":") as [SortKey, SortOrder];
  return { key, order };
}
