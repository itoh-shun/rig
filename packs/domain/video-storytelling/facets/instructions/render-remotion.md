# instruction: render-remotion

**Remotion で動画コンポジションを生成する skill（render target = remotion）。** 演出（台本）は `video-direct` が、作法は `video-director` persona が持つ。本 instruction は **TSX で書く Composition の構造契約**だけを定める。

> Remotion は React コンポーネントとして動画を書き、`@remotion/cli` の `npx remotion render` で MP4 にエンコードするフレームワーク。React 文化のあるプロジェクトに馴染む。Remotion 単体は OSS（MIT）。`@remotion/licensing` は商用利用条件に注意（公式参照）。

## 認証契約（厳守）

生成する Remotion プロジェクトは次を**正確に**満たすこと（外すと render が壊れる）：

1. **エントリ**: `src/Root.tsx` で `<Composition>` を 1 つ以上登録する。各 `<Composition>` は `id` / `component` / `durationInFrames` / `fps` / `width` / `height` / `defaultProps` を持つ。fps は 30 または 60 を既定。`durationInFrames = 秒 × fps`。
2. **コンポーネントは「現在フレーム」駆動**: `useCurrentFrame()` で時間を取り、CSS の `transform` / `opacity` を計算する。**実時計（`requestAnimationFrame` / `setTimeout`）禁止**（renderer はフレームを seek するので壊れる）。アニメは `interpolate(frame, [in, out], [v0, v1])` で書く。
3. **シーン分割**: `<Sequence from={f} durationInFrames={d}>` でシーン窓を切る。`from` / `durationInFrames` はフレーム単位。
4. **メディア**: `<Video src={staticFile('clip.mp4')} />` / `<Audio src={staticFile('music.mp3')} />` / `<Img src={staticFile('logo.svg')} />`。アセットは `public/` に置く（`staticFile()` の解決元）。
5. **フォント**: `@remotion/google-fonts` か `loadFont()` で明示読込。CSS ファミリだけ書くと render 時にフォントが無くて崩れる。
6. **設定**: `remotion.config.ts` で `setVideoImageFormat('jpeg')`、`setOverwriteOutput(true)`、必要なら `setCodec('h264')`。`tsconfig.json` の `jsx` は `react-jsx`。

## 手順

1. **設計図の確保** — `video-direct` の制作台本（シーン表＝尺/構図/テロップ/VO/BGM）を起点に。無ければ先に台本を起こす。
2. **プロジェクト雛形** — 既存に Remotion プロジェクトが無ければ生成: `package.json`（`remotion` / `@remotion/cli` / `@remotion/google-fonts` / `react` / `react-dom`）、`src/index.ts`（`registerRoot`）、`src/Root.tsx`（`<Composition>` 登録）、`remotion.config.ts`、`public/`。出力先は `video/<name>/`。
3. **Composition の実装** — 台本の各シーンを `<Sequence>` でラップし、内部で `useCurrentFrame()` + `interpolate()` で入り/強調/退き。テロップは Sequence 内に `<AbsoluteFill>` で配置、VO は `<Audio>` を一段重ねる。**screen ショット**は `<Video src={staticFile('screen.mp4')} muted />` か CSS で組んだ seekable モック端末（フレーム駆動）。
4. **同梱物** — `STORYBOARD.md`（台本）と `README.md`（`npm i && npx remotion preview` / `npx remotion render <id> out.mp4` 手順・assets の置き場・要件 Node 22+ / FFmpeg / Chrome）を出す。
5. **引き渡し** — 「この harness では render しない。手元で `npm i && npx remotion preview` で確認 → `npx remotion render` で MP4」と明示する。商用利用の場合は Remotion ライセンス条件（公式 `remotion.dev/license`）を確認するよう一言添える。

## 出力構造（例）

```
video/<name>/
  package.json
  tsconfig.json
  remotion.config.ts
  src/
    index.ts
    Root.tsx
    compositions/
      Trailer.tsx
      scenes/Hero.tsx
      scenes/Reveal.tsx
  public/
    music.mp3
    screen.mp4
    logo.svg
  STORYBOARD.md
  README.md
```

## ガード

- **実時計禁止**（必ず `useCurrentFrame()` + `interpolate()`／renderer はフレーム seek 駆動）。
- **アセット参照は `staticFile()`**（相対パス直書きは render 時に解決されない）。
- **フォントは明示読込**（CSS 任せにしない）。
- **各ビートを実機能に紐づける**（ソース対応表・空ワード禁止）。
- **動いている画面ショットを最低 1 つ**（`<Video>` 実録 or seekable モック）。
- render 環境（Node22+/FFmpeg/Chrome）と、harness で MP4 を出さない旨を必ず伝える。**Remotion の商用利用条件**を一言添える（License Server / Apache-2.0 例外等は公式に従う）。

## 関連

- `facets/instructions/video-direct` — 制作台本（このスキルの設計図元）。
- `facets/personas/video-director` — 演出（target 非依存）。
- `facets/instructions/render-hyperframes` — HTML 即プレビュー＋ HyperFrames コンポジション（互換 OSS / Apache-2.0・per-render 料金なし）。React 文化のないプロジェクトでは hyperframes 既定が素直。
