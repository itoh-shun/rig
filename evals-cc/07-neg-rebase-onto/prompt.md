---
schema_version: "1.0"
name: 07-neg-rebase-onto
description: git の用語説明だけの依頼で rig のフローが起動しないこと
tags: [routing, no-fire, ja]
max_turns: 8
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---
git rebase --onto の3つの引数がそれぞれ何を指すのか教えてほしい。実際の操作はしないので、意味だけでいい。
