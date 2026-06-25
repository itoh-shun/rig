#!/usr/bin/env node
// Build per-worker dispatch packets from group_spec.json.
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import yaml from "node:util";

const PROJECT = process.cwd();
const gs = JSON.parse(readFileSync(join(PROJECT, "group_spec.json"), "utf8"));
const fd = gs.film_direction || "";

const tokensCss = readFileSync(join(PROJECT, "design-system/chunks/tokens.css"), "utf8");
const easingsJs = readFileSync(join(PROJECT, "design-system/chunks/easings.js"), "utf8");
const voiceMd = existsSync(join(PROJECT, "design-system/chunks/voice.md"))
  ? readFileSync(join(PROJECT, "design-system/chunks/voice.md"), "utf8")
  : "";

const sharedHeader = [
  "## Film direction",
  fd,
  "",
  "## Tokens/easings/voice",
  "### tokens.css",
  tokensCss,
  "### easings.js",
  easingsJs,
  "### voice.md",
  voiceMd,
  "",
].join("\n");

writeFileSync(join(PROJECT, ".dispatch/scene-shared.txt"), sharedHeader);
if (!sharedHeader.includes("--brand-primary")) {
  console.error("FATAL: scene-shared.txt missing --brand-primary (tokens.css not loaded?)");
  process.exit(1);
}

mkdirSync(join(PROJECT, ".dispatch/scene-dispatch"), { recursive: true });

// Per worker: shared header + Scenes YAML (verbatim from group_spec.json.groups[i].scenes)
const yamlDump = (obj) => {
  // simple YAML serializer for the structure prep emits (objects + arrays + scalars)
  const ind = (n) => "  ".repeat(n);
  const dump = (v, depth) => {
    if (v === null || v === undefined) return "null";
    if (typeof v === "string") {
      if (v.includes("\n") || v.length > 80) {
        return "|\n" + v.split("\n").map((l) => ind(depth + 1) + l).join("\n");
      }
      // quote if it has special chars
      return /^[\w./:-]+$/.test(v) ? v : JSON.stringify(v);
    }
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    if (Array.isArray(v)) {
      if (v.length === 0) return "[]";
      return "\n" + v.map((x) => ind(depth) + "- " + dump(x, depth + 1).replace(/^\n/, "")).join("\n");
    }
    if (typeof v === "object") {
      const keys = Object.keys(v);
      if (keys.length === 0) return "{}";
      return "\n" + keys.map((k) => ind(depth) + `${k}: ` + dump(v[k], depth + 1).replace(/^\n/, "")).join("\n");
    }
    return JSON.stringify(v);
  };
  return dump(obj, 0).replace(/^\n/, "");
};

for (const g of gs.groups) {
  const packet = [
    sharedHeader,
    `Scenes:`,
    yamlDump(g.scenes),
  ].join("\n");
  writeFileSync(join(PROJECT, `.dispatch/scene-dispatch/${g.worker_id}.txt`), packet);
}

console.log(`✓ shared header: ${sharedHeader.length} chars`);
console.log(`✓ per-worker packets: ${gs.groups.length}`);
for (const g of gs.groups) {
  console.log(`  ${g.worker_id}: composition=${g.composition_id} file=${g.composition_file} dur=${g.duration_s}s scenes=${g.scene_ids.join(",")}`);
}
