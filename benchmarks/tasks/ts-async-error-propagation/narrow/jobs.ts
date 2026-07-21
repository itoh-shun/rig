type Loader<T> = () => Promise<T>;
type Reporter = (error: unknown) => void;

export async function runJob<T>(load: Loader<T>, report: Reporter): Promise<T | null> {
  try {
    return await load();
  } catch (error) {
    report(error);
    if (error instanceof Error && error.message === "reported outage") {
      throw error;
    }
    return null;
  }
}
