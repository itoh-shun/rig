# spd — SPDドメイン拡張パック（opt-in）

SPD（Supply Processing and Distribution＝病院の物品物流管理）ドメインの**業界知識**と**6ステークホルダー・ペルソナ**を定義した、rig の**拡張パック**です。

**rig 本体には同梱されません（デフォルトでは入らない）。** このディレクトリは rig plugin の読み込み対象外（`skills/` / `commands/` の外）に置かれており、使いたい人だけが下記の方法で自分の rig に注入します。単体でも self-contained な Claude Code skill として動きます（rig 非依存）。

## 中身

```
extensions/spd/
├── SKILL.md                     # skill 本体（発動条件・2モード・手順）
├── README.md                    # このファイル
└── references/
    ├── verdict-format.md        # 親の集約フォーマット（GO/条件付きGO/要再検討）
    ├── personas/                # 6ステークホルダー定義（出力形式インライン）
    │   ├── hospital-executive.md   # 病院経営層・事務長
    │   ├── materials-manager.md    # 用度課・材料部（購買）
    │   ├── ward-nurse.md           # 看護現場（病棟師長）
    │   ├── spd-operator.md         # SPD現場責任者（センター長）
    │   ├── spd-vendor-manager.md   # SPD事業者の経営者
    │   └── distributor.md          # 医療材料卸・ディーラー
    └── knowledge/               # 業界知識（DESCRIPTIVE）
        ├── spd-basics.md           # 定義・業務範囲・定数管理・運営形態・償還・GS1
        ├── spd-industry.md         # 日本SPD協議会・業界構造・行政動向・業界課題
        ├── spd-glossary.md         # 用語集（ユビキタス言語）
        └── domain-template.md      # 自院/自社固有の記入用テンプレ（空）
```

## rig への注入（opt-in インストール）

### 方法1: `/rig:import`（推奨）

rig の取り込み機構で注入する。出所とハッシュが `skills-lock.json` に記録され、`--check-updates` で上流差分検知の対象になる。

```
/rig:import ./extensions/spd                       # この repo を clone 済みの場合
/rig:import itoh-shun/rig --path extensions/spd    # GitHub から直接
```

import は本パックを rig ブリック（persona / knowledge / instruction / output-contract / recipe / command）へ翻訳して配置する。取り込み後は `/rig:catalog` に現れる。

### 方法2: project / user 層へ手動コピー

rig のブリック解決層（project → user → shipped）に直接置く。

| 同梱物 | コピー先（project 層） |
|---|---|
| `references/personas/*.md` | `<repo>/.claude/rig/personas/spd/*.md` |
| `references/knowledge/*.md` | `<repo>/.claude/rig/knowledge/domain/spd/*.md` |

user 層に置く場合は `~/.claude/rig/personas/spd/` を使う。注入後は `/rig:go --persona spd/ward-nurse` のように persona を都度投入するか、manifest の `default_personas` で常時投入できる。

### アンインストール

- 方法1で入れた場合: 生成されたブリックを削除し、`skills-lock.json` の該当エントリを除去。
- 方法2で入れた場合: コピーしたファイルを削除するだけ。

## 単体利用（rig なし）

このディレクトリ一式を Claude Code の skill として読み込めば、rig なしでも動く（`SKILL.md` の手順は rig 語彙に依存しない）。

## 出所・ライセンス

- 業界知識の出典は各 knowledge ファイル末尾の `sources` に記載（一般社団法人 日本SPD協議会 https://www.spdjapan.org/ ほか公開資料。最終確認 2026-07-24）。ペルソナは公開情報に基づく**架空の役割定義**であり、実在の個人・団体の見解ではない。
- ライセンスはリポジトリルートの `LICENSE`（MIT）を継承する。
