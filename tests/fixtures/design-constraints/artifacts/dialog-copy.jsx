import { Button, Dialog, Stack } from "../ui";

export function RetryDialog({ onRetry, onClose }) {
  return (
    <Dialog labelledBy="retry-title">
      <Stack gap="var(--spacing-md)">
        <h2 id="retry-title">通信が切れました</h2>
        <p>ネットワークを確認してから、もう一度お試しください。</p>
        <p>復旧しない場合はこちらの手順で管理者に連絡してください。</p>
        <p>Click the room name to open its details.</p>
        <p>Please review the details before saving.</p>
        <p>保存を選ぶと、入力内容がその場で反映されます。</p>
        <p>ボタンの押下中は、状態が分かるように表示を保ちます。</p>
        <Button onClick={onRetry}>もう一度試す</Button>
        <Button onClick={onClose}>閉じる</Button>
      </Stack>
    </Dialog>
  );
}
