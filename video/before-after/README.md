# rig: before / after — HyperFrames composition

rig を使った開発体験の **before / after** を示す紹介動画。[HeyGen HyperFrames](https://github.com/heygen-com/hyperframes)（OSS・Apache-2.0）で**本物の MP4** にレンダリングできる。`/rig:movie --hyperframes` 生成物の一例。

> アニメは GSAP タイムライン(`paused:true`)を `window.__timelines["rig-ba"]` に登録（renderer がフレームごとに seek する契約）。**実時計（`requestAnimationFrame`/`setTimeout`）は使わない**。

## 必要なもの
Node.js 22+ ／ FFmpeg ／ headless Chrome（Puppeteer 自動取得）

## 使い方
```bash
npx hyperframes preview                              # ブラウザでライブプレビュー
npx hyperframes render --output renders/before-after.mp4   # MP4 書き出し
```

## カスタマイズ
- **テロップ/尺**：各 `.scene`（`class="clip"`＋`data-start`/`data-duration`）と末尾 GSAP タイムラインを編集。
- **実画面を“実録”に**：`s3`（before）/ `s5`・`s6`（after）の seekable モック端末を、画面収録 mp4 に差し替えると説得力が最大（`index.html` 末尾のコメント例）。before は素の手作業、after は実際の `rig` 出力を撮る。
- **BGM**：`assets/music.wav` を置き `<audio …>` を足す。

## 別経路（ゼロインストール）
**即見たい**なら `web/before-after.html` をブラウザで開く（同内容の HTML プレビュー・MP4 は出ないが install 不要）。
