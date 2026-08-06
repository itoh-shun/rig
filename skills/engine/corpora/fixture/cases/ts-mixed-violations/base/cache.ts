export interface Entry {
  key: string;
  value: number;
  expiresAt: number;
}

export function sortEntries(entries: Entry[]): Entry[] {
  return [...entries].sort((a, b) => a.key.localeCompare(b.key));
}

export function isExpired(entry: Entry, now: number): boolean {
  return entry.expiresAt <= now;
}

export async function loadEntries(source: {
  fetchAll: () => Promise<Entry[]>;
}): Promise<Entry[]> {
  const entries = await source.fetchAll();
  return sortEntries(entries);
}

export function summarize(entries: Entry[]): string {
  return entries.map((entry) => `${entry.key}=${entry.value}`).join(",");
}
