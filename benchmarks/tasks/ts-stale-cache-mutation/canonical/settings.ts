const settings = new Map<string, string>([
  ["alice", "light"],
  ["bob", "compact"],
]);
const cache = new Map<string, string>();

export function getSetting(userId: string): string | undefined {
  if (cache.has(userId)) {
    return cache.get(userId);
  }
  const value = settings.get(userId);
  if (value !== undefined) {
    cache.set(userId, value);
  }
  return value;
}

export function updateSetting(userId: string, value: string): string {
  settings.set(userId, value);
  cache.delete(userId);
  return value;
}
