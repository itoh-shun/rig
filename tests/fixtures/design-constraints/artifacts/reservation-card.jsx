import { Button, Card, Icon, Link, Stack } from "../ui";

export function ReservationCard({ room, start, detailHref, onCancel }) {
  return (
    <Card>
      <Stack gap="var(--spacing-sm)">
        <>
          <h3 style={{ color: "var(--color-text-body)" }}>{room}</h3>
          <p style={{ color: "var(--color-text-muted)" }}>{start} 開始</p>
        </>
        <Icon name="calendar" aria-hidden="true" />
        <Link href={detailHref}>予約の詳細を開く</Link>
        <Button onClick={onCancel}>この予約を取り消す</Button>
      </Stack>
    </Card>
  );
}
