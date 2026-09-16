---
schema_version: "1.0"
name: 05-preserve-behaviour
description: 振る舞いを変えない書き換えで、先に現状を固定してから比較するか
tags: [routing, fire, ja]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---
金額計算のユーティリティが読みにくくなってきたので書き直したい。振る舞いは一切変えたくないので、壊れていないことを確かめながら進めたい。どういう段取りになる？
