# instruction: render-aviutl

**AviUtl 拡張編集向けのプロジェクト素材を生成する skill（render target = aviutl・stub 契約・日本語コミュニティ向け）。** 演出（台本）は `video-direct` が、作法は `video-director` persona が持つ。本 instruction は **AviUtl 拡張編集が読める `.exo` プロジェクト**と関連スクリプト断片の契約だけを定める。

> AviUtl は KEN くん作のフリーウェア動画編集ソフト＋拡張編集プラグイン。`.exo`（オブジェクトファイル、テキスト形式・INI 風）でタイムラインオブジェクトを記述、`.tra`（トラックバー）／ `.anm`（アニメーション効果スクリプト・Lua）でモーション拡張。日本語コミュニティが厚く、ニコニコ系コンテンツでよく使われる。

> ステータス: **stub**（v0.x で契約のみ・厚いノウハウは今後）。エージェントは `.exo` プロジェクトと `.anm` / `.obj` スクリプト断片まで生成し、AviUtl 環境で読み込んで仕上げる前提。

## 認証契約（厳守）

生成する素材は次の構造を**正確に**満たすこと：

1. **拡張編集プロジェクト**: `video/<name>/aviutl/<name>.exo`。INI 風テキスト形式で、`[exedit]`（フレーム数・解像度・FPS）と `[0]`〜`[N]`（各オブジェクト＝シーン）を持つ。`start` / `end` はフレーム単位、`layer` で重なり順。
2. **オブジェクトのタイプ**:
   - **テキスト**: `_name=テキスト` ＋ `text=` でテロップ。フォント・サイズ・色は属性で。
   - **画像/動画**: `_name=画像ファイル` / `動画ファイル` ＋ `file=`（パスは AviUtl 起動環境からの絶対 or 相対）。
   - **図形**: `_name=図形` で背景帯やキーラインを作る。
   - **シーンチェンジ**: `_name=シーンチェンジ` ＋ `type=` でトランジション。
3. **アニメーション効果（任意）**: `.anm` Lua スクリプトを `video/<name>/aviutl/script/` に置き、`.exo` の対象オブジェクトの後段フィルタとして参照する。フェード・スライドなど。
4. **アセット**: `video/<name>/aviutl/assets/` に画像・動画・音源・フォント。`.exo` 内のパスは `${PROJECT}/assets/...` 形式の相対参照を推奨（AviUtl 起動環境でユーザーが置換）。
5. **メタ**: `STORYBOARD.md`（台本・シーン表・ソース対応表）と `README.md`（AviUtl + 拡張編集の前提・インポート手順・フレームレート設定）。

## 手順（stub）

1. **設計図の確保** — `video-direct` の制作台本を起点に。
2. **基本設定の決定** — 解像度（既定 1920x1080）・フレームレート（既定 30fps）。AviUtl は CFR 前提なので、台本の秒数 × FPS でフレーム化する。
3. **`.exo` 生成** — 上記契約どおりオブジェクトを並べる。1 シーン = 1〜数オブジェクト（背景帯＋テロップ＋画像など）。レイヤは下から背景→画像→テロップ→トランジションの順を既定。
4. **アニメ効果（任意）** — 「フェードイン 0.3 秒」程度なら `.exo` 標準フィルタ、複雑なら `.anm` Lua 断片を出力。
5. **同梱物** — `STORYBOARD.md` / `README.md`（AviUtl + 拡張編集の入手元・インポート手順・assets パスの調整方法）。
6. **引き渡し** — 「この harness では AviUtl を起動しない／書き出さない。`.exo` を AviUtl 拡張編集にドラッグドロップで読み込み → assets パス調整 → プラグイン出力で MP4」と明示する。

## 出力構造（例）

```
video/<name>/aviutl/
  <name>.exo
  assets/
    bgm.wav
    screen.mp4
    logo.png
  script/
    slide-in.anm        # 任意
  STORYBOARD.md
  README.md
```

## ガード（stub・最低限）

- **harness は AviUtl を操作しない**（プラグイン経由とも）。`.exo` テキストと `.anm` スクリプト生成に徹する。
- **パスは相対 + 注記**（AviUtl 環境でユーザーが置換する旨を `.exo` 先頭コメントと README に明示）。
- **フレームレート CFR**（AviUtl 前提）。可変フレームレート（VFR）の動画ソースは事前に CFR 化が要る旨を README に書く。
- **各ビートを実出所に紐づける**（ソース対応表・空ワード禁止）— target を問わない `video-director` の規律を継承。
- **動いている画面ショット**は assets 内の実録 mp4 で。harness ではモックは作らず、録画依頼を明記する。

## 既知の限界（v0.x stub の正直な開示）

- `.exo` テンプレート集は薄い（テロップ / 帯背景 / シーンチェンジの最低限のみ）。
- `.anm` Lua の生成は数パターンに限定（slide-in / fade）。
- 出力プラグイン（x264guiEx 等）の設定は生成しない（ユーザー側で持つ前提）。
- 日本語フォント前提のレイアウト計算（縦書き・ルビ等）は未対応。
- ノウハウが厚くなったら HyperFrames / Remotion 並みの契約に昇格する。

## 関連

- `facets/instructions/video-direct` — 制作台本（このスキルの設計図元）。
- `facets/personas/video-director` — 演出（target 非依存）。
- `facets/instructions/render-hyperframes` — エージェント完結（render まで）の OSS 経路。
- AviUtl 公式: [AviUtlのお部屋](http://spring-fragrance.mints.ne.jp/aviutl/) / 拡張編集プラグイン。
