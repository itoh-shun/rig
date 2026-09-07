import { Button, Card, ConfirmBar, Stack } from "../ui";

export function CancelFlow({ onKeep, onCancel }) {
  return (
    <Card>
      <Stack gap="var(--spacing-md)">
        <h2>予約を取り消しますか</h2>
        <p>取り消すと同じ時間帯は他の人が予約できるようになります。</p>
        <Button onClick={onKeep}>予約を残す</Button>
        <ConfirmBar onConfirm={onCancel} />
      </Stack>
    </Card>
  );
}
