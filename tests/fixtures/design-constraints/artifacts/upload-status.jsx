import { Stack, Toast } from "../ui";

export function UploadStatus({ failed, fileName }) {
  if (!failed) {
    return <Toast tone="info">{fileName} を取り込みました。</Toast>;
  }
  return (
    <Stack gap="var(--spacing-sm)">
      <Toast tone="danger">エラーが{"発生しました"}。</Toast>
      <p style={{ color: "var(--color-text-muted)" }}>{fileName}</p>
    </Stack>
  );
}
