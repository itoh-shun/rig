---
type: llm
weight: 1
---
最終メッセージが asyncio.gather と asyncio.wait の違いを実際に説明していれば合格。最低限、次の2点の差に触れていること。

- 戻り値の形。gather は結果を渡した順のリストで返し、wait は (done, pending) の Task 集合を返す。
- 例外の扱い。gather は既定で最初の例外を呼び出し側へ伝播させ、wait は例外を送出せず完了した Task の側に保持する。

説明をせずに「開発フローを組みます」「どの recipe で進めますか」といった作業手順の話に逸れている応答は不合格。
