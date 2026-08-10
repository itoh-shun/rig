# policy: secure-provider-execution

この recipe の headless provider 実行は、生成役と検証役の両方について、利用者が確認した
絶対 executable path と SHA-256 を実行前に明示しなければ開始しません。script の場合は
shebang interpreter の絶対 path と SHA-256 も必要です。

runtime は PATH から executable を探索せず、確認した bytes を sealed descriptor に固定して
実行します。prompt は stdin だけで渡し、provider ごとの環境変数 allowlist と固定 PATH、
read-only isolation flags を使います。`cmd` と identity を証明できない opaque provider は
拒否します。pin 不足や検証失敗を通常の provider 実行へ downgrade してはいけません。
親 process も goal 本文を argv の `--goal` から受け取らず、`--goal-stdin` の有界な
非 TTY UTF-8 入力から一度だけ読みます。

machine 固有の path、digest、credential は recipe や pack に記録しません。pin は gitignore
された local 0600 config または同等の明示 CLI flags からだけ受け取ります。
