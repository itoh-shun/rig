# instruction: hyperframes-video

**HyperFrames（HeyGen の OSS・HTML→決定論的 MP4 レンダラ）でリリース動画を MP4 出力可能なコンポジションとして生成する skill。** `release-movie`（HTML 即プレビュー＋制作台本）の**高品質レンダリング経路**。演出の作法は `release-director` persona が持つ（Native-first）。

> HyperFrames は素の HTML/CSS/JS を **headless Chrome が各フレームを seek してスクショ → ffmpeg でエンコード**して MP4 にする（"same input, same frames, same output"）。React 不要・Apache-2.0・per-render 料金なし。要 Node 22+ / FFmpeg / headless Chrome。**この harness では render できない**（コンポジション一式まで生成し、render はユーザー環境で `npx hyperframes render`）。

## 認証契約（厳守・公式仕様）

生成する `index.html` は次を**正確に**満たすこと（これを外すと render が壊れる）：

1. **ルート要素**：`<div id="root" data-composition-id="<id>" data-start="0" data-width="1920" data-height="1080">`。
2. **タイムド要素（各シーン/クリップ）**：`class="clip"` ＋ `data-start`（秒）＋ `data-duration`（秒）＋ `data-track-index`（重なり順）。表示はこの時間窓で管理される。
3. **アニメは seekable に**：実時計（`requestAnimationFrame`/`setTimeout`）で動かさない。**GSAP のタイムラインを `paused: true` で作り、`window.__timelines["<id>"]` に登録**する（renderer がフレームごとに seek する）。GSAP は CDN で読む：`<script src="https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js"></script>`。
   - 各シーンの入り/強調は `tl.from(...)` / `tl.fromTo(...)` を**位置パラメータ（秒）**で配置（例 `tl.from("#s2", {opacity:0, y:40, duration:0.8}, 5)`）。
   - 「タイプ風」は実時計でなく、行を GSAP の `stagger` で順次 opacity 表示する（seek 可能・決定論的）。
4. **音声**：`<audio class="clip" data-start data-duration data-track-index data-volume src="assets/music.wav">`（ミックスして書き出される）。
5. **実画面（最良＝実録 mp4）**：`<video class="clip" data-start data-duration data-track-index src="assets/screen.mp4" muted playsinline>`。**「動いている画面必須」は、ここに実際の画面収録 mp4 を入れるのが本筋**（モック端末は CSS＋GSAP で seekable に作る代替）。

## 手順

1. **素材の収集** — 対象リリース（既定 CHANGELOG 最新エントリ・バージョン指定可）。`release-movie` の制作台本（シーン表＝尺/テロップ/VO/BGM/ソース対応表）が既にあればそれを設計図に使う。無ければ先に台本を起こす。
2. **コンポジション生成** — 上記契約どおり `video/<name>/index.html` を生成（または同梱例 `video/launch-film/` を複製して中身を差し替え）。各台本シーン → `class="clip"`＋`data-start`/`data-duration`、入りは GSAP タイムラインに配置。**screen ショットは `<video>` 実録枠 or seekable モック端末を最低1つ**（`release-movie` の必須ルールを継承）。
3. **同梱物** — `STORYBOARD.md`（台本）と `README.md`（`npx hyperframes init/preview/render` 手順・要件・assets の置き場）を出す。
4. **引き渡し** — 「この harness では render 不可。手元で `npm i && npx hyperframes preview` で確認 → `npx hyperframes render --output renders/out.mp4`」と明示する。BGM/実録 mp4 は `assets/` に置けば取り込まれる旨を添える。

## ガード

- **認証契約を厳守**（`data-composition-id`/`class="clip"`/`window.__timelines` を外さない）。renderer は seek 駆動なので**実時計アニメは禁止**（必ず GSAP タイムライン or CSS/WAAPI の seekable な手段）。
- **各ビートを実機能に紐づける**（ソース対応表・捏造機能を作らない）・**空ワード禁止**（`release-director` と同じ規律）。
- **動いている画面ショットを最低1つ**（実録 mp4 が最良・無ければ seekable モック）。
- render 環境（Node22+/ffmpeg/Chrome）が要る旨と、**harness では MP4 を出せない**旨を必ず伝える（誇張しない）。

## 関連

- `facets/instructions/release-movie` — HTML 即プレビュー＋制作台本（このスキルの設計図元）。`/rig:movie --hyperframes` でこの経路に入る。
- `facets/personas/release-director` — 演出（ハイプだが嘘なし・目玉1つ・テロップ短く）。
- 同梱例：`video/launch-film/`（rig の HyperFrames コンポジション・GSAP seekable・README つき）。
