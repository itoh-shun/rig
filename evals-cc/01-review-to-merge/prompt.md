---
schema_version: "1.0"
name: 01-review-to-merge
description: 自然文のレビュー→マージ依頼が rig:engine に載り、recipe と gate を決め切るか
tags: [routing, fire, ja]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---
これから書いたコードをレビューして、マージまで持っていきたい。レビュー観点の抜けが怖いので、どういう手順で進めることになるかを先に見せてほしい。
