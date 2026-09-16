---
schema_version: "1.0"
name: 08-probe-imperative-bugfix
description: 命令形の依頼で description が routing するかを切り分ける探り
tags: [probe, routing, fire, ja]
max_turns: 10
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill]
runs: 1
---
チェックアウトの合計金額が、カートが空のときに 0 ではなく NaN になる。直してほしい。同じバグを捕まえる回帰テストも付けて。
