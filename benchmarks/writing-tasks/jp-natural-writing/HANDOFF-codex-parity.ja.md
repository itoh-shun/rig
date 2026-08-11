# Codex への依頼文 — parity track（コピーして渡す）

このファイルは依頼文そのもの。以下をそのまま Codex に貼る。

---

`parity.py`（PR #391 / ブランチ `agent/japanese-parity-benchmark`）の train raw baseline を取り終えた。
結果は `benchmarks/writing-tasks/jp-natural-writing/results/train-raw.json`、コミット `514f020`。

依頼は「この負け方から Japanese Rules v2 を作る」こと。ただし**そのまま作ると測定が壊れる**ので、
先に下の「罠」を読んでほしい。

## 実行した構成

```
reference : gpt-5.6-sol      (codex exec, -o でファイル出力)
candidate : claude-sonnet-5  (claude -p)
judge     : claude-opus-5    (claude -p)
split     : train (10 cases / 20 judgments)
fingerprint : 355cb0aba284
```

```bash
cd benchmarks/writing-tasks/jp-natural-writing
python3 parity.py --split train \
  --checkpoint /tmp/jp-parity-train-raw.checkpoint.json \
  --json-out results/train-raw.json
```

argv は fingerprint に含まれる。`parity.providers.example.json` を変えると checkpoint は
再利用されない。結果 JSON は生成した config とセットでしか意味を持たないので、両方同じコミットに入れてある。

### 実行前に直した欠陥（重要）

例として同梱されていた provider 設定は `claude -p` をローカル設定込みで起動していた。
Stop hook が `decision: "block"` を返す環境では追加ターンが強制され、`--output-format text` が拾う
最終メッセージが hook のコメントに置き換わる。**Writer の候補文も Judge の判定も hook のコメントになる。
エラーは出ない。全ケースが静かに汚染される。** `DEFAULT_CONFIG` がこの example を指しているので、
既定の実行が壊れていた。

対処として `claude` に `--safe-mode`、`codex` に `--ignore-user-config` を追加した。
今回の結果は修正後の構成で取っている（Judge の理由文に実際の Claude の日本語が全10ケース分引用されており、
hook 由来のテキストは混入していない）。

## 結果

```
candidate preference:  22.5%   (ci95_by_case 0.0-48.3%)
                       50.0% = parity
order consistency    :  90.0%

dimensions
  conciseness   17.5%
  naturalness   32.5%
  context_fit   37.5%
  correctness   37.5%
  tone          45.0%

勝ち: technical_explanation 100% / rewrite 100%
負け: business_chat 25% / 他7カテゴリすべて 0%
```

区間の上限が 50% をわずかに下回る。「GPT 優位、ただしパリティとの区別はぎりぎり」が正確な読み。
N=10 であることと、Judge と Candidate が同ベンダーであることは割り引いて読んでほしい。

## 罠 — 負けの半分は日本語の問題ではない

Judge の理由文を原因別に割ると、こうなる。

**① 出力の枠組みで負けた（4/10）** — Judge が明示的に減点理由として書いている。

| case | Judge が指摘した内容 |
|---|---|
| `politeness` | 「汎用的な文面を作成します」という前置きが余計 |
| `ambiguity` | 「〜返信文の例です」という前置きで、依頼された返信そのものになっていない |
| `incident_report` | 「作成しました」という前置き＋依頼にない見出し・箇条書き |
| `support_reply` | 「以下、文面案です」＋区切り線・太字・「必要であれば調整します」 |

**② 日本語・内容で負けた（4/10）**

| case | Judge が指摘した内容 |
|---|---|
| `code_review` | 依頼文にない「型上も null 許容になっている箇所」を断定（前提の捏造） |
| `conciseness` | 「確認したところ」を削り、情報を落とさず短縮せよという条件に違反 |
| `casual` | 情報の重複、全角「！」と半角「?」の混在 |
| `business_chat` | 読点で謝罪をつなぐ形がやや不自然（僅差） |

ここから素直に Rules v2 を作ると、中身は大半が
「成果物だけ出せ・前置き禁止・見出し禁止・末尾の申し出禁止」になる。
それは `candidate_preference` を大きく動かすが、**日本語品質は一切改善しない**。

さらに厄介なのは、**それが holdout でも通ってしまう**こと。
形式順守はどの split でも一般化するからだ。train→dev→holdout の分割はベンチマークの丸暗記を
検出するための設計だが、これは丸暗記ではなく本物の一般的な挙動変化なので、この設計では捕まらない。
「holdout でも改善した＝日本語品質が上がった」という推論が、この一点だけ成立しない。

## 依頼したいこと

1. `results/train-raw.json` の `case_results[].judges[].reasons` を全件読んで、
   上の①/②の割り当てが妥当か独立に検証してほしい。私の分類を疑ってよい。
2. Rules v2 を**軸ごとに分けて**書いてほしい。枠組み軸（前置き・装飾の抑制）と
   言語軸（自然さ・簡潔さ・敬語の距離感・前提の捏造の抑制）を別セクションにする。
3. 以降の dev / holdout の改善幅は、**軸ごとに分解して報告できる形**にしたい。
   分解せずに単一の `candidate_preference` だけで語ると、測っているものが
   日本語品質ではなく CLI の出力作法になる。
4. 枠組み軸だけで parity 付近まで到達してしまう可能性がある。その場合
   「Claude の日本語が GPT に並んだ」ではなく「出力形式を揃えたら差が消えた」が正しい結論になる。
   その線引きをどう検証するかの設計も欲しい。

## 消せない非対称（仕様として扱う）

- `--ignore-user-config` を付けても `$CODEX_HOME/skills` は読み込まれる
  （`Skill descriptions were shortened to fit the 2% skills context budget` の警告が出る）。
  Reference 側だけ skills が常駐している。
- `--safe-mode` で hooks / CLAUDE.md / skills / plugins / MCP は落ちるが、
  **Claude Code の既定のエージェント用システムプロンプトは残る**。
  「以下、文面案です」を生んでいるのはこれで、フラグでは到達できない。
  一方 `codex exec -o` は成果物だけをファイルに書く設計になっている。
  上の①はここに由来する可能性が高い。修正待ちではなく、測定条件として扱ってほしい。

## まだ実行していないもの

- `--split dev`（raw / rules 適用後の両方）
- `--split holdout` — ルールが確定するまで一度も触らない。
