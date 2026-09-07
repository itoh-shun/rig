import { Card, Field, Stack, Toast } from "../ui";

type Entry = { id: string; label: string };

export function SettingsPanel(props: { entries: Array<Entry> }) {
  const index: Map<string, Entry> = new Map();
  const pick = <T,>(xs: T[]): T | undefined => xs[0];
  const style = {
    fontFamily: "var(--font-body)",
    backgroundColor: "var(--color-surface)",
    padding: "var(--spacing-md)",
  };

  return (
    <Card style={style}>
      <Stack>
        {props.entries.map((e) => (
          <Field key={e.id} label={e.label} />
        ))}
      </Stack>
      <Toast>保存しました</Toast>
    </Card>
  );
}
