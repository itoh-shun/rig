#!/usr/bin/env node
// Pad each scene WAV with trailing silence to match the OLD scene durations
// (the visual compositions and group_spec were authored against the longer
// Kokoro-based timings). Voice ends, visual settles, transition takes over.
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, renameSync } from "node:fs";
import { join } from "node:path";

const PROJECT = process.cwd();
const groupSpec = JSON.parse(readFileSync(join(PROJECT, "group_spec.json"), "utf8"));
const audioMeta = JSON.parse(readFileSync(join(PROJECT, "audio_meta.json"), "utf8"));

// Old scene durations: from group_spec.json.groups[*].scenes[sid].estimatedDuration_s
const oldDur = {};
for (const g of groupSpec.groups) {
  for (const [sid, s] of Object.entries(g.scenes)) {
    oldDur[sid] = s.estimatedDuration_s;
  }
}

let totalNew = 0;
for (const [sid, info] of Object.entries(audioMeta.scenes)) {
  const target = oldDur[sid];
  if (target == null) {
    console.warn(`✗ no target duration for ${sid}, skipping`);
    continue;
  }
  const inWav = join(PROJECT, info.voicePath);
  const cur = info.voiceDuration;
  if (cur >= target - 0.01) {
    // already long enough; nothing to pad (rare — we may need to trim instead)
    if (cur > target + 0.01) {
      console.warn(`  ⚠ ${sid}: current ${cur.toFixed(3)}s exceeds target ${target.toFixed(3)}s by ${(cur - target).toFixed(3)}s — trimming`);
      const tmp = inWav + ".tmp.wav";
      const r = spawnSync("ffmpeg", [
        "-y", "-i", inWav,
        "-ar", "44100", "-ac", "1", "-sample_fmt", "s16",
        "-t", String(target),
        tmp,
      ], { stdio: ["ignore", "ignore", "pipe"] });
      if (r.status !== 0) {
        console.error(`  ✗ trim failed: ${r.stderr?.toString().slice(-200)}`);
        process.exit(1);
      }
      renameSync(tmp, inWav);
      audioMeta.scenes[sid].voiceDuration = target;
      totalNew += target;
      console.log(`  ${sid}: ${cur.toFixed(3)}s → trimmed to ${target.toFixed(3)}s`);
      continue;
    }
    audioMeta.scenes[sid].voiceDuration = cur;
    totalNew += cur;
    console.log(`  ${sid}: already matches (${cur.toFixed(3)}s)`);
    continue;
  }
  // pad with trailing silence
  const pad = target - cur;
  const tmp = inWav + ".tmp.wav";
  const r = spawnSync(
    "ffmpeg",
    [
      "-y",
      "-i", inWav,
      "-af", `apad=pad_dur=${pad.toFixed(6)}`,
      "-ar", "44100", "-ac", "1", "-sample_fmt", "s16",
      "-t", String(target),
      tmp,
    ],
    { stdio: ["ignore", "ignore", "pipe"] }
  );
  if (r.status !== 0) {
    console.error(`✗ pad ${sid} failed`);
    process.exit(1);
  }
  renameSync(tmp, inWav);
  audioMeta.scenes[sid].voiceDuration = target;
  totalNew += target;
  console.log(`  ${sid}: ${cur.toFixed(3)}s → padded to ${target.toFixed(3)}s (+${pad.toFixed(3)}s silence)`);
}

audioMeta.total_duration_s = parseFloat(totalNew.toFixed(3));
audioMeta._padding_applied = "trailing silence to match pre-Irodori scene durations";
writeFileSync(join(PROJECT, "audio_meta.json"), JSON.stringify(audioMeta, null, 2));
console.log(`\n✓ total: ${audioMeta.total_duration_s}s (matches group_spec)`);
