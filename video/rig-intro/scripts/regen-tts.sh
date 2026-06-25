#!/usr/bin/env bash
# Regenerate per-scene TTS at speed 1.4 (faster Japanese, closer to 75s target)
# and rebuild audio_meta.json by hand. Bypasses audio.mjs which hardcodes speed=1.0.
set -euo pipefail

cd "$(dirname "$0")/.."

SPEED="${SPEED:-1.4}"
VOICE=jf_alpha
LANG=ja

mkdir -p assets/voice
rm -f assets/voice/*.wav assets/voice/*_words.json

SCENES=$(node -e 'const s=require("./narrator_scripts.json").scenes;console.log(JSON.stringify(s.map(x=>({n:x.sceneNumber,t:x.script}))))')
TOTAL=0
META='{"tts_provider":"kokoro","voice_id":"jf_alpha","bgm_provider":null,"bgm_enabled":false,"bgm_path":null,"bgm_pending":false,"bgm_log":null,"bgm_pid":null,"bgm_mode":null,"bgm_target_duration_s":null,"bgm_seed_duration_s":null,"bgm_loop_count":null,"speed":'"$SPEED"',"scenes":{}}'

echo "$SCENES" | node -e '
  const arr = JSON.parse(require("fs").readFileSync(0,"utf8"));
  arr.forEach(s => console.log(`${s.n}\t${s.t}`));
' > /tmp/scenes.tsv

while IFS=$'\t' read -r n t; do
  out="assets/voice/scene_${n}.wav"
  echo "→ scene_${n} (speed ${SPEED}): $t"
  npx hyperframes tts "$t" --voice "$VOICE" --lang "$LANG" --speed "$SPEED" --output "$out" >/dev/null 2>&1 || {
    echo "  ✗ FAIL scene_${n}" >&2
    continue
  }
  dur=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$out")
  printf '  duration: %.3fs\n' "$dur"
  echo "$n $dur $out" >> /tmp/scene-durations.txt
done < /tmp/scenes.tsv

echo "--- rebuilding audio_meta.json ---"
node <<'NODE'
const fs = require('fs');
const lines = fs.readFileSync('/tmp/scene-durations.txt','utf8').trim().split('\n');
const scenes = {};
let total = 0;
for (const ln of lines) {
  const [n, d, p] = ln.split(/\s+/);
  const dur = parseFloat(d);
  total += dur;
  scenes[`scene_${n}`] = { voicePath: p, voiceDuration: dur, wordsPath: "" };
}
const meta = {
  tts_provider: "kokoro", voice_id: "jf_alpha",
  bgm_provider: null, bgm_enabled: false, bgm_path: null, bgm_pending: false,
  bgm_log: null, bgm_pid: null, bgm_mode: null,
  bgm_target_duration_s: null, bgm_seed_duration_s: null, bgm_loop_count: null,
  speed: parseFloat(process.env.SPEED || '1.4'),
  total_duration_s: parseFloat(total.toFixed(3)),
  scenes,
};
fs.writeFileSync('audio_meta.json', JSON.stringify(meta, null, 2));
console.log(`total: ${meta.total_duration_s}s across ${Object.keys(scenes).length} scenes`);
NODE

rm -f /tmp/scene-durations.txt
