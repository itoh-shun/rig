type Loader<T> = () => Promise<T>;
type Reporter = (error: unknown) => void;

export async function runJob<T>(load: Loader<T>, report: Reporter): Promise<T> {
  try {
    return await load();
  } catch (error) {
    report(error);
    throw error;
  }
}
