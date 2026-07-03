# instruction: render-davinci

**DaVinci Resolve 向けの素材一式を生成する skill（render target = davinci・stub 契約・人間編集者引き渡し前提）。** 演出（台本）は `video-direct` が、作法は `video-director` persona が持つ。本 instruction は **DaVinci に渡せる中間表現**の契約だけを定める。

> DaVinci Resolve はプロ向け NLE。Fusion ノードでのモーション、カラーグレード、Fairlight オーディオ、Studio 版の Lua / Python スクリプト API（`fusionscript` / DaVinciResolveScript） を持つ。harness はアプリ操作はしない＝**Fusion comp / Lua / Python script の素材まで生成し、最終編集と書き出しは人間（または DaVinci 起動環境のスクリプト実行）に引き渡す**。

> ステータス: **stub**（v0.x で契約のみ・厚いノウハウは今後）。HyperFrames / Remotion で完結する用途を既定に、DaVinci は「人間編集者の作業を加速する素材出し」を主目的にする。

## 認証契約（厳守）

生成する素材は次の構造を**正確に**満たすこと（Fusion / Resolve スクリプトの仕様準拠）：

1. **Fusion Composition**: `*.comp` または `*.setting` を `video/<name>/fusion/` に配置。テキスト形式のノードグラフ（Background / Text+ / Merge / KeyFrame で構成）。テロップ・タイトル・ロゴアニメをここで作る。
2. **Resolve スクリプト**（任意・自動化向け）: Lua または Python を `video/<name>/scripts/` に配置。`resolve = bmd.scriptapp("Resolve")` で起点を取り、プロジェクト作成・タイムライン作成・メディアプール投入を行う。**実行は DaVinci 環境**（`Workspace > Console` / `fuscript` / `-> Tools > Scripts`）。
3. **タイムライン EDL / XML**: 長尺で人間編集者が起点に使うなら `*.edl`（CMX 3600）または `*.fcpxml`（Final Cut Pro XML、Resolve がインポート可）を `video/<name>/edl/` に。各シーンの in/out をフレーム時刻で記録。
4. **アセット**: 実録 mp4・音源・画像・モック動画は `video/<name>/assets/` に。Resolve はそのまま読める。
5. **メタ**: `STORYBOARD.md`（台本・シーン表・ソース対応表）と `README.md`（Resolve でのインポート手順・要 DaVinci Studio かどうか）。

## 手順（stub）

1. **設計図の確保** — `video-direct` の制作台本を起点に。
2. **編集モデル決定** — (a) **Fusion comp のみ**（モーション/タイトル素材だけ、Cut Page で人間が並べる）、(b) **EDL/XML タイムライン**（in/out まで harness が決めて Edit Page にロード）、(c) **Resolve スクリプト**（プロジェクト構築まで自動化）。既定は (a)。
3. **素材生成** — 上記契約どおりファイル群を出力。Fusion comp はテキスト編集可能な形式（`*.setting`）を優先（diff レビュー可能）。
4. **同梱物** — `STORYBOARD.md` / `README.md`（インポート手順・必要な DaVinci 版（Free / Studio）の明示・スクリプト API 利用時は Studio 必須に注意）。
5. **引き渡し** — 「この harness では DaVinci を起動しない／書き出さない。素材は `video/<name>/` に揃えた。人間編集者が DaVinci で読み込み → 並べ → カラー / オーディオ → Deliver で書き出し」と明示する。

## 出力構造（例）

```
video/<name>/
  assets/
    music.wav
    screen.mp4
  fusion/
    hero-title.setting
    reveal-callout.setting
  edl/
    timeline.fcpxml         # 任意
  scripts/
    setup_project.py        # 任意・要 DaVinci Studio
  STORYBOARD.md
  README.md
```

## ガード（stub・最低限）

- **harness は Resolve を操作しない**（API/UI 経由とも）。素材生成と引き渡しに徹する。
- **Fusion comp はテキスト形式を優先**（バイナリ `.drp` は harness では作らない）。
- **DaVinci Free / Studio の差を README に明記**（Resolve スクリプト API は Studio 限定）。
- **各ビートを実出所に紐づける**（ソース対応表・空ワード禁止）— target を問わない `video-director` の規律を継承。
- **動いている画面ショット**は assets 内の実録 mp4 で。harness ではモック動画を作らず、無ければ録画依頼を明記する。

## 既知の限界（v0.x stub の正直な開示）

- Fusion comp のテンプレート集はまだ薄い（hero-title / lower-third / reveal の最低限のみを想定）。
- カラーグレードのプリセット（.drx）生成は未対応。
- Fairlight オートメーション（EQ / Compressor）生成は未対応。
- ノウハウが厚くなったら HyperFrames / Remotion 並みの契約に昇格する。

## 関連

- `facets/instructions/video-direct` — 制作台本（このスキルの設計図元）。
- `facets/personas/video-director` — 演出（target 非依存）。
- `facets/instructions/render-hyperframes` — エージェント完結（render まで）の OSS 経路。
- DaVinci 公式: [Resolve Scripting Documentation](https://documents.blackmagicdesign.com/UserManuals/DaVinciResolve17_ScriptingDocumentation.pdf)（要 Studio）。
