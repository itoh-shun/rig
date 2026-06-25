#!/usr/bin/env node
// Regenerate per-scene TTS using Irodori-TTS (Docker, ref-wav cloning).
// Reads ./dialogue.json (host/guest exchanges per scene), generates each
// line via the irodori-tts:latest Docker image, concatenates per-scene
// WAVs with a small inter-line pause, resamples to 44100Hz mono, and
// rebuilds audio_meta.json. Replaces the existing assets/voice/scene_*.wav.
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, rmSync, existsSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

const PROJECT = process.cwd();
const REFS_DIR = "/home/itoshun/projects/ai-podcast/data/irodori-refs";
const HF_CACHE = `${process.env.HOME}/.cache/huggingface`;
const HF_CHECKPOINT = "Aratako/Irodori-TTS-500M-v2";
const IMAGE = "irodori-tts:latest";

const dlg = JSON.parse(readFileSync(join(PROJECT, "dialogue.json"), "utf8"));
const speakerCfg = {
  host: { name: "モコ", ref_wav: dlg._meta.host_ref, seed: dlg._meta.host_seed, cfg: dlg._meta.cfg_scale_speaker },
  guest: { name: "ハナ", ref_wav: dlg._meta.guest_ref, seed: dlg._meta.guest_seed, cfg: dlg._meta.cfg_scale_speaker },
};
const PAUSE = dlg._meta.inter_line_pause_s ?? 0.25;
const NUM_STEPS = dlg._meta.num_steps ?? 40;

const lineDir = join(PROJECT, "assets", "voice-irodori-lines");
const voiceDir = join(PROJECT, "assets", "voice");
mkdirSync(lineDir, { recursive: true });
mkdirSync(voiceDir, { recursive: true });

// clean per-scene wavs (user-owned; safe). Line dir contains root-owned files
// from a prior run, so clean it via docker root.
for (const f of readdirSync(voiceDir)) {
  if (f.startsWith("scene_") && f.endsWith(".wav")) rmSync(join(voiceDir, f));
}
{
  const r = spawnSync("docker", ["run", "--rm", "-v", `${lineDir}:/work`, "alpine", "sh", "-c", "rm -f /work/*.wav"], {
    stdio: ["ignore", "ignore", "pipe"],
  });
  if (r.status !== 0) {
    console.error("✗ failed to clean lineDir via docker root:", r.stderr?.toString());
    process.exit(1);
  }
}

const UID = process.getuid();
const GID = process.getgid();

function dockerSynth(text, refWav, seed, cfg, outAbsHost) {
  // mount lineDir as /workspace/outputs and write under it; run as host user so
  // node can later clean up (otherwise docker writes as root and breaks rm).
  // Run as root inside the container (image's venv is root-owned). After all
  // lines are generated we chown the outputs back to the user via a final
  // docker root call so we can clean up on the next run.
  const args = [
    "run", "--rm", "--gpus", "all",
    "-v", `${lineDir}:/workspace/outputs`,
    "-v", `${REFS_DIR}:/workspace/refs:ro`,
    "-v", `${HF_CACHE}:/workspace/hf-cache`,
    "-e", "HF_HOME=/workspace/hf-cache",
    "-e", "PYTHONIOENCODING=utf-8",
    "-e", "LANG=C.UTF-8",
    "-e", "LC_ALL=C.UTF-8",
    IMAGE,
    "--hf-checkpoint", HF_CHECKPOINT,
    "--text", text,
    "--ref-wav", `/workspace/refs/${refWav}`,
    "--num-steps", String(NUM_STEPS),
    "--cfg-scale-speaker", String(cfg),
    "--tail-window-size", "40",
    "--tail-std-threshold", "0.02",
    "--tail-mean-threshold", "0.05",
    "--seed", String(seed),
    "--output-wav", `/workspace/outputs/${outAbsHost.split("/").pop()}`,
  ];
  const r = spawnSync("docker", args, { stdio: ["ignore", "pipe", "pipe"] });
  if (r.status !== 0) {
    console.error(`✗ docker failed: ${r.stderr?.toString().slice(-400)}`);
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

function ffmpegConcatAndResample(inWavs, outWav, pauseSec) {
  // Build a filter_complex that concats with silence padding between lines, resampled to 44100 mono
  // Approach: use concat filter with silence pads via aevalsrc inputs
  const inputs = [];
  const filterParts = [];
  inWavs.forEach((p, i) => {
    inputs.push("-i", p);
    filterParts.push(`[${i}:a]aresample=44100,aformat=channel_layouts=mono,asetpts=N/SR/TB[a${i}]`);
  });
  // silence input
  inputs.push("-f", "lavfi", "-t", String(pauseSec), "-i", "anullsrc=r=44100:cl=mono");
  const silIdx = inWavs.length;
  filterParts.push(`[${silIdx}:a]asetpts=N/SR/TB[sil]`);
  // build concat sequence: a0, sil, a1, sil, a2, ...
  let seq = "";
  let n = 0;
  inWavs.forEach((_, i) => {
    if (i > 0) {
      seq += `[sil]`;
      n++;
    }
    seq += `[a${i}]`;
    n++;
  });
  filterParts.push(`${seq}concat=n=${n}:v=0:a=1[outa]`);
  const args = [
    "-y",
    ...inputs,
    "-filter_complex", filterParts.join(";"),
    "-map", "[outa]",
    "-ar", "44100", "-ac", "1", "-sample_fmt", "s16",
    outWav,
  ];
  const r = spawnSync("ffmpeg", args, { stdio: ["ignore", "pipe", "pipe"] });
  if (r.status !== 0) {
    console.error("✗ ffmpeg concat failed:", r.stderr?.toString().slice(-500));
    return false;
  }
  return true;
}

const scenes = {};
let total = 0;
let lineCounter = 0;

for (const [sid, lines] of Object.entries(dlg.scenes)) {
  console.log(`\n=== ${sid} (${lines.length} line${lines.length > 1 ? "s" : ""}) ===`);
  const lineWavs = [];
  for (const [idx, ln] of lines.entries()) {
    const cfg = speakerCfg[ln.speaker];
    if (!cfg) {
      console.error(`✗ unknown speaker '${ln.speaker}' in ${sid}`);
      process.exit(1);
    }
    const outName = `${String(lineCounter).padStart(3, "0")}_${sid}_${idx}_${ln.speaker}.wav`;
    const outAbs = join(lineDir, outName);
    process.stdout.write(`  → ${cfg.name}: ${ln.text}\n`);
    const ok = dockerSynth(ln.text, cfg.ref_wav, cfg.seed, cfg.cfg, outAbs);
    if (!ok) {
      console.error(`  ✗ FAIL line ${lineCounter}`);
      process.exit(1);
    }
    const d = ffprobeDur(outAbs);
    console.log(`    ${d.toFixed(3)}s  (${outName})`);
    lineWavs.push(outAbs);
    lineCounter++;
  }
  // concat (or copy single)
  const sceneOut = join(voiceDir, `${sid}.wav`);
  if (lineWavs.length === 1) {
    // resample single
    spawnSync(
      "ffmpeg",
      ["-y", "-i", lineWavs[0], "-ar", "44100", "-ac", "1", "-sample_fmt", "s16", sceneOut],
      { stdio: ["ignore", "ignore", "pipe"] }
    );
  } else {
    if (!ffmpegConcatAndResample(lineWavs, sceneOut, PAUSE)) process.exit(1);
  }
  const sceneDur = ffprobeDur(sceneOut);
  total += sceneDur;
  scenes[sid] = { voicePath: `assets/voice/${sid}.wav`, voiceDuration: sceneDur, wordsPath: "" };
  console.log(`  ✓ ${sid}: ${sceneDur.toFixed(3)}s`);
}

// Write audio_meta.json
const meta = {
  tts_provider: "irodori",
  voice_id: "host=モコ_vd guest=ハナ_vd",
  bgm_provider: null, bgm_enabled: false, bgm_path: null, bgm_pending: false,
  bgm_log: null, bgm_pid: null, bgm_mode: null,
  bgm_target_duration_s: null, bgm_seed_duration_s: null, bgm_loop_count: null,
  inter_line_pause_s: PAUSE,
  total_duration_s: parseFloat(total.toFixed(3)),
  scenes,
};
writeFileSync(join(PROJECT, "audio_meta.json"), JSON.stringify(meta, null, 2));
console.log(`\n✓ total: ${meta.total_duration_s}s across ${Object.keys(scenes).length} scenes`);

// Chown root-owned line files back to the user so the next run can clean them
// without needing sudo (line files are root because docker ran as root).
{
  const r = spawnSync(
    "docker",
    ["run", "--rm", "-v", `${lineDir}:/work`, "alpine", "sh", "-c", `chown -R ${UID}:${GID} /work`],
    { stdio: ["ignore", "ignore", "pipe"] }
  );
  if (r.status !== 0) {
    console.warn("⚠ chown back to user failed (not fatal):", r.stderr?.toString().trim());
  }
}
