#!/usr/bin/env node
// v4: rewrite the caption from scratch using the same pattern/granularity as
// the shipped ハナ caption (which produces a clean voice). Try Japanese and
// English variants, and use long random seeds like the shipped ones
// (2143939058999994603 / 1697770221922499424 / ...).
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";

const PROJECT = process.cwd();
const HF_CACHE = `${process.env.HOME}/.cache/huggingface`;
const HF_CHECKPOINT = "Aratako/Irodori-TTS-500M-v2-VoiceDesign";
const IMAGE = "irodori-tts:latest";

const outDir = join(PROJECT, "assets", "voice-male-samples");
mkdirSync(outDir, { recursive: true });

const SAMPLE_TEXT = "rig は LEGO 式。タスクごとに、開発フローを組み立てるオーケストレータです。";

// Each variant pairs a caption with a long random seed (16-19 digits, matching shipped style).
// Captions are rewritten to mirror the shipped ハナ pattern: [age + role] voice with [signature timbre], [timbre descriptor], [intonation], [delivery], [warmth/tone].
const VARIANTS = [
  {
    tag: "A-en-corporate-narrator",
    caption:
      "Mature middle-aged male voice with a calm composed gravity, deep low-pitched broadcaster timbre, steady measured natural intonation, articulate clear professional delivery, warm trustworthy news-anchor tone with reassuring composure and approachable maturity.",
    seed: 8147239851072834561n,
  },
  {
    tag: "B-en-deep-baritone",
    caption:
      "Deep adult male voice with a rich chest-resonant warmth, low baritone broadcaster timbre, smooth grounded natural intonation, articulate measured authoritative delivery, calm gentlemanly narrator tone with mature warmth and approachable depth.",
    seed: 6193847265928374619n,
  },
  {
    tag: "C-ja-news-anchor",
    caption:
      "40代男性のニュースキャスターの声。落ち着いた低音の重厚な声質と、滑舌の良いアナウンサー調の澄んだ音色を持ち、自然で穏やかな抑揚、丁寧で聞き取りやすい中程度のテンポで、信頼感のある真面目で誠実な語り口。",
    seed: 5719283746159372845n,
  },
  {
    tag: "D-ja-corporate-narrator",
    caption:
      "30代後半男性の企業ナレーターの声。落ち着いた中低音の温かみのある声質と、ビジネス向けの明瞭な音色を持ち、自然で安定した抑揚、丁寧で聞き取りやすいやや遅めのテンポで、信頼感のある誠実で親しみやすい語り口。",
    seed: 9382647519283746591n,
  },
];

const CFG_SCALE_CAPTION = 4.0;
const NUM_STEPS = 60;

const UID = process.getuid();
const GID = process.getgid();

for (const v of VARIANTS) {
  const outName = `male-v4-${v.tag}.wav`;
  const outPath = join(outDir, outName);
  if (existsSync(outPath)) {
    console.log(`skip (exists): ${outName}`);
    continue;
  }
  console.log(`→ ${v.tag} (seed ${v.seed})`);
  console.log(`   caption: ${v.caption.slice(0, 90)}...`);
  const args = [
    "run", "--rm", "--gpus", "all",
    "-v", `${outDir}:/workspace/outputs`,
    "-v", `${HF_CACHE}:/workspace/hf-cache`,
    "-e", "HF_HOME=/workspace/hf-cache",
    "-e", "PYTHONIOENCODING=utf-8",
    "-e", "LANG=C.UTF-8",
    "-e", "LC_ALL=C.UTF-8",
    IMAGE,
    "--hf-checkpoint", HF_CHECKPOINT,
    "--text", SAMPLE_TEXT,
    "--caption", v.caption,
    "--no-ref",
    "--seed", v.seed.toString(),
    "--num-steps", String(NUM_STEPS),
    "--cfg-scale-caption", String(CFG_SCALE_CAPTION),
    "--cfg-guidance-mode", "alternating",
    "--tail-window-size", "40",
    "--tail-std-threshold", "0.02",
    "--tail-mean-threshold", "0.05",
    "--output-wav", `/workspace/outputs/${outName}`,
  ];
  const r = spawnSync("docker", args, { stdio: ["ignore", "inherit", "inherit"] });
  if (r.status !== 0) {
    console.error(`✗ ${v.tag} failed (exit ${r.status})`);
  }
}

spawnSync(
  "docker",
  ["run", "--rm", "-v", `${outDir}:/work`, "alpine", "sh", "-c", `chown -R ${UID}:${GID} /work`],
  { stdio: ["ignore", "ignore", "pipe"] }
);

console.log("\nv4 Samples:");
for (const f of readdirSync(outDir).sort()) {
  if (f.endsWith(".wav") && f.includes("v4")) {
    const dur = spawnSync(
      "ffprobe",
      ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", join(outDir, f)],
      { stdio: ["ignore", "pipe", "ignore"] }
    ).stdout.toString().trim();
    console.log(`  ${f}: ${parseFloat(dur).toFixed(2)}s`);
  }
}
