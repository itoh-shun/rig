---
schema_version: "1.0"
name: 02-fix-nan-total
description: バグ報告の形の依頼で、作業が隔離され回帰テストが先に置かれるか
tags: [routing, fire, en]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---
There's a bug in our checkout code: when the cart is empty, the total renders as NaN instead of 0. I want this fixed properly rather than patched over, with a test that would have caught it. How would you go about it?
