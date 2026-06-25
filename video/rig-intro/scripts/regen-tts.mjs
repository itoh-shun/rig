#!/usr/bin/env node
// Regenerate per-scene TTS via direct `hyperframes tts` with --speed override,
// then rebuild audio_meta.json. Bypasses audio.mjs which hardcodes speed=1.0.
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, rmSync, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

const SPEED = parseFloat(process.env.SPEED || "1.4");
const VOICE = "jf_alpha";
const LANG = "ja";

const root = process.cwd();
const ns = JSON.parse(readFileSync(join(root, "narrator_scripts.json"), "utf8"));
const voiceDir = join(root, "assets", "voice");
mkdirSync(voiceDir, { recursive: true });

// Clean old
for (const f of (existsSync(voiceDir) ? readdirSync(voiceDir) : [])) {
  rmSync(join(voiceDir, f));
}

const scenes = {};
let total = 0;
const failed = [];

for (const s of ns.scenes) {
  const n = s.sceneNumber;
  const out = join("assets", "voice", `scene_${n}.wav`);
  process.stdout.write(`→ scene_${n} (speed ${SPEED}): ${s.script}\n`);
  const r = spawnSync(
    "npx",
    ["hyperframes", "tts", s.script, "--voice", VOICE, "--lang", LANG, "--speed", String(SPEED), "--output", out],
    { cwd: root, stdio: ["ignore", "pipe", "pipe"] }
  );
  if (r.status !== 0) {
    failed.push({ n, stderr: (r.stderr || "").toString().slice(-500) });
    process.stdout.write(`  ✗ FAIL (exit ${r.status})\n`);
    continue;
  }
  const dur = parseFloat(
    spawnSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", out], {
      cwd: root,
    })
      .stdout.toString()
      .trim()
  );
  total += dur;
  scenes[`scene_${n}`] = { voicePath: out, voiceDuration: dur, wordsPath: "" };
  process.stdout.write(`  ${dur.toFixed(3)}s\n`);
}

const meta = {
  tts_provider: "kokoro",
  voice_id: VOICE,
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
  speed: SPEED,
  total_duration_s: parseFloat(total.toFixed(3)),
  scenes,
};
writeFileSync(join(root, "audio_meta.json"), JSON.stringify(meta, null, 2));
console.log(`\n✓ total: ${meta.total_duration_s}s across ${Object.keys(scenes).length} scenes`);
if (failed.length) {
  console.log(`✗ failed: ${failed.length}`);
  failed.forEach(({ n, stderr }) => console.log(`  scene_${n}: ${stderr.replace(/\n/g, " ")}`));
}
