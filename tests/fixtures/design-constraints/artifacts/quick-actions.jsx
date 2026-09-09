import { Button, Card, Stack } from "../ui";

export function QuickActions({ onBook, onSearch }) {
  return (
    <Card>
      <Card.Header title="よく使う操作" />
      <Stack gap="var(--spacing-sm)">
        <Button onClick={onBook}>今すぐ予約する</Button>
        <Button onClick={onSearch}>空き時間を探す</Button>
      </Stack>
    </Card>
  );
}
