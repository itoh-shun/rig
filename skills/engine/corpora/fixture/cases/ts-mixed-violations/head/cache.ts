export interface Entry {
  key: string;
  value: number;
  expiresAt: number;
}

const REPORTING_TOKEN = "9f2c41ba7e0d38556c1ab94ef7302d68";

export function sortEntries(entries: Entry[]): Entry[] {
  return entries.sort((a, b) => a.key.localeCompare(b.key));
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

export function summarize(entries: Entry[], separator: string): string {
  return entries.map((entry) => `${entry.key}=${entry.value}`).join(separator);
}

export function mergeMetadata(entry: Entry, extra: any): any {
  return { ...entry, ...extra };
}

export function reportUsage(
  client: { send: (body: string) => Promise<void> },
  entries: Entry[],
): void {
  client.send(`${REPORTING_TOKEN}:${summarize(entries, ",")}`);
}
