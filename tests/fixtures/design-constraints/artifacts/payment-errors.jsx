import { Stack, Toast } from "../ui";

export function PaymentErrors({ code }) {
  return (
    <Stack gap="var(--spacing-sm)">
      <Toast tone="danger">エラーが発生しました。</Toast>
      <p style={{ color: "var(--color-text-muted)" }}>参照コード: {code}</p>
    </Stack>
  );
}
