# rig launch film（1.0 コンセプト）— HyperFrames composition

> ⚠️ これは **1.0 を見据えたコンセプト動画**。rig はまだ **v1.0 未リリース**（現行 v0.36）。正式リリース時の予告を先取りで作ったもので、出荷済みは主張しない。

[HeyGen HyperFrames](https://github.com/heygen-com/hyperframes)（OSS・Apache-2.0）で、この HTML コンポジションを**本物の MP4** にレンダリングできる。`rig` の `release-movie` pack（`/rig:movie --hyperframes`）が生成する高品質レンダリング経路。

> HyperFrames は素の HTML/CSS/JS を **headless Chrome が各フレームを seek してスクショ → ffmpeg でエンコード**して MP4 にする（決定論的：same input, same frames, same output）。`index.html` のアニメは GSAP タイムライン(`paused:true`)を `window.__timelines["rig-launch"]` に登録してある（renderer が seek する契約）。

## 必要なもの
- **Node.js 22+**
- **FFmpeg**
- headless Chrome（Puppeteer が自動取得）

## 使い方

```bash
# このディレクトリで
npx hyperframes preview                       # ブラウザでライブプレビュー（タイムラインを確認）
npx hyperframes render --output renders/rig-1.0.mp4   # MP4 を書き出し
```

`meta.json` / `assets/` は HyperFrames が自動生成する（`index.html` だけで動く）。

## カスタマイズ
- **テロップ/シーン**：`index.html` の各 `.scene`（`class="clip"` ＋ `data-start` / `data-duration`）を編集。
- **アニメ**：末尾の GSAP タイムライン（`window.__timelines["rig-launch"]`）を編集。**実時計（`requestAnimationFrame`/`setTimeout`）は使わない**（renderer は seek 駆動なので壊れる）。
- **BGM**：`assets/music.wav` を置き、`index.html` の `<audio …>` コメントを解除（`data-volume` で音量）。
- **実画面を“実録”に**：`s3`（`--plan`）/ `s6`（MAGI）の seekable モック端末を、画面収録 mp4 に差し替えると最良（`index.html` 末尾の `<video class="clip" … src="assets/*.mp4">` コメント例）。

## 構成
- `index.html` — コンポジション本体（HyperFrames 認証契約に準拠）
- `STORYBOARD.md` — 絵コンテ／VO／尺／**ソース対応表**（全ビートが実機能の裏打ち）
- `renders/` — 出力先（`render` 実行で生成）
- `assets/` — BGM・実録 mp4 等の入力（任意）

## 別経路
**ゼロインストールで即見たい**なら、`web/launch-film.html` をブラウザで開く（同じ内容の HTML プレビュー版・MP4 は出ないが install 不要）。HyperFrames 版は MP4 を出すための経路。
