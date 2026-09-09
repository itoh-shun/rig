import { Card, Stack } from "../ui";

const ACCENT = "#" + "0a84ff";

export function ThemeAccent({ children }) {
  return (
    <Card style={{ borderColor: ACCENT }}>
      <Stack gap="var(--spacing-sm)">{children}</Stack>
    </Card>
  );
}
