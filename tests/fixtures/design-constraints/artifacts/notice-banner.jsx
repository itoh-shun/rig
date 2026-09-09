import { Card, Icon, Stack } from "../ui";

export function NoticeBanner({ title, body }) {
  return (
    <Card style={{ background: "var(--color-surface-muted)" }}>
      <Stack gap="var(--spacing-sm)">
        <Icon name="info" style={{ color: "#FF3B30" }} />
        <p style={{ color: "var(--color-text-body)" }}>{title}</p>
        <p style={{ color: "var(--color-text-muted)" }}>{body}</p>
      </Stack>
    </Card>
  );
}
