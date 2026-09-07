import { Card, Stack } from "../ui";

export function PrintPreview({ title, lines }) {
  return (
    <Card style={{ background: "#FFFFFF" }}>
      <Stack gap="var(--spacing-md)">
        <h2 style={{ color: "#1B1F23" }}>{title}</h2>
        {lines.map((line) => (
          <p key={line} style={{ color: "var(--color-text-muted)" }}>
            {line}
          </p>
        ))}
      </Stack>
    </Card>
  );
}
