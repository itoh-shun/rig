#!/usr/bin/env node
// Regenerate per-scene TTS using Irodori-TTS VoiceDesign mode (no ref-wav,
// caption-conditioned). Hard-coded to the v4-D config the user picked.
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync, renameSync } from "node:fs";
import { join } from "node:path";

const PROJECT = process.cwd();
const HF_CACHE = `${process.env.HOME}/.cache/huggingface`;
const HF_CHECKPOINT = "Aratako/Irodori-TTS-500M-v2-VoiceDesign";
const IMAGE = "irodori-tts:latest";

// v4-D config — 30代後半 男性 企業ナレーター
const CAPTION =
  "30代後半男性の企業ナレーターの声。落ち着いた中低音の温かみのある声質と、ビジネス向けの明瞭な音色を持ち、自然で安定した抑揚、丁寧で聞き取りやすいやや遅めのテンポで、信頼感のある誠実で親しみやすい語り口。";
const SEED = 9382647519283746591n;
const CFG_SCALE_CAPTION = 4.0;
const NUM_STEPS = 60;

const dlg = JSON.parse(readFileSync(join(PROJECT, "dialogue.json"), "utf8"));
const lineDir = join(PROJECT, "assets", "voice-irodori-lines-vd");
const voiceDir = join(PROJECT, "assets", "voice");
mkdirSync(lineDir, { recursive: true });
mkdirSync(voiceDir, { recursive: true });

// clean per-scene wavs (user-owned)
for (const f of readdirSync(voiceDir)) {
  if (f.startsWith("scene_") && f.endsWith(".wav")) {
    try {
      const fp = join(voiceDir, f);
      const r = spawnSync("rm", ["-f", fp]);
      if (r.status !== 0) console.warn(`rm ${f} failed`);
    } catch (e) {}
  }
}
// clean line dir via docker root (in case prior runs left root-owned files)
spawnSync("docker", ["run", "--rm", "-v", `${lineDir}:/work`, "alpine", "sh", "-c", "rm -f /work/*.wav"], {
  stdio: ["ignore", "ignore", "pipe"],
});

const UID = process.getuid();
const GID = process.getgid();

function synthesize(text, outName) {
  const args = [
    "run", "--rm", "--gpus", "all",
    "-v", `${lineDir}:/workspace/outputs`,
    "-v", `${HF_CACHE}:/workspace/hf-cache`,
    "-e", "HF_HOME=/workspace/hf-cache",
    "-e", "PYTHONIOENCODING=utf-8",
    "-e", "LANG=C.UTF-8",
    "-e", "LC_ALL=C.UTF-8",
    IMAGE,
    "--hf-checkpoint", HF_CHECKPOINT,
    "--text", text,
    "--caption", CAPTION,
    "--no-ref",
    "--seed", SEED.toString(),
    "--num-steps", String(NUM_STEPS),
    "--cfg-scale-caption", String(CFG_SCALE_CAPTION),
    "--cfg-guidance-mode", "alternating",
    "--tail-window-size", "40",
    "--tail-std-threshold", "0.02",
    "--tail-mean-threshold", "0.05",
    "--output-wav", `/workspace/outputs/${outName}`,
  ];
  const r = spawnSync("docker", args, { stdio: ["ignore", "pipe", "pipe"] });
  if (r.status !== 0) {
    console.error(`✗ synth failed: ${(r.stderr || "").toString().slice(-300)}`);
    return false;
  }
  return true;
}

function ffprobeDur(p) {
  const r = spawnSync(
    "ffprobe",
    ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", p],
    { stdio: ["ignore", "pipe", "ignore"] }
  );
  return parseFloat(r.stdout.toString().trim());
}

function ffmpegResampleAndCopy(inWav, outWav) {
  const r = spawnSync(
    "ffmpeg",
    ["-y", "-i", inWav, "-ar", "44100", "-ac", "1", "-sample_fmt", "s16", outWav],
    { stdio: ["ignore", "ignore", "pipe"] }
  );
  if (r.status !== 0) {
    console.error("✗ ffmpeg resample failed:", r.stderr?.toString().slice(-300));
    return false;
  }
  return true;
}

const scenes = {};
let total = 0;
let lineCounter = 0;

for (const [sid, lines] of Object.entries(dlg.scenes)) {
  if (lines.length !== 1) {
    console.error(`✗ ${sid}: voicedesign mode requires single-line scenes only`);
    process.exit(1);
  }
  const text = lines[0].text;
  const outName = `${String(lineCounter).padStart(3, "0")}_${sid}.wav`;
  const outAbs = join(lineDir, outName);
  console.log(`→ ${sid}: ${text}`);
  if (!synthesize(text, outName)) process.exit(1);
  const sceneOut = join(voiceDir, `${sid}.wav`);
  if (!ffmpegResampleAndCopy(outAbs, sceneOut)) process.exit(1);
  const dur = ffprobeDur(sceneOut);
  total += dur;
  scenes[sid] = { voicePath: `assets/voice/${sid}.wav`, voiceDuration: dur, wordsPath: "" };
  console.log(`  ${dur.toFixed(3)}s`);
  lineCounter++;
}

// chown root-owned line files
spawnSync(
  "docker",
  ["run", "--rm", "-v", `${lineDir}:/work`, "alpine", "sh", "-c", `chown -R ${UID}:${GID} /work`],
  { stdio: ["ignore", "ignore", "pipe"] }
);

const meta = {
  tts_provider: "irodori-voicedesign",
  voice_id: "v4-D 30代後半 企業ナレーター",
  voicedesign_config: {
    caption: CAPTION,
    seed: SEED.toString(),
    cfg_scale_caption: CFG_SCALE_CAPTION,
    num_steps: NUM_STEPS,
    cfg_guidance_mode: "alternating",
  },
  bgm_provider: null,
  bgm_enabled: false,
  bgm_path: null,
  bgm_pending: false,
  bgm_log: null,
  bgm_pid: null,
  bgm_mode: null,
  bgm_target_duration_s: null,
  bgm_seed_duration_s: null,
  bgm_loop_count: null,
  total_duration_s: parseFloat(total.toFixed(3)),
  scenes,
};
writeFileSync(join(PROJECT, "audio_meta.json"), JSON.stringify(meta, null, 2));
console.log(`\n✓ total: ${meta.total_duration_s}s across ${Object.keys(scenes).length} scenes`);
