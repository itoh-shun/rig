---
schema_version: "1.0"
name: 04-finish-to-pr
description: ブランチ完了から PR までの依頼で、検査が push より前に来るか
tags: [routing, fire, en]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---
I'm done with this branch. Take it through to a pull request — but I don't want anything pushed that hasn't been checked first. What happens, in order?
