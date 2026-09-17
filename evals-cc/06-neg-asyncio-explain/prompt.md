---
schema_version: "1.0"
name: 06-neg-asyncio-explain
description: 説明だけの依頼で rig のフローが起動しないこと（過剰発火の検出）
tags: [routing, no-fire, ja]
max_turns: 8
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---
Python の asyncio.gather と asyncio.wait の違いを説明してほしい。コードは変更しなくていい、説明だけでいい。
