# output-contract: review-verdict

## output-contract: review-verdict

The output structure every reviewer that names this contract obeys — security, design, test, and
the rest. **Evidence-first**: the grounds, each with an anchor, come first and the verdict comes
last, because writing the verdict first commits to a conclusion before the reasoning that should
produce it. Machine extraction reads the `判定:` and `確信度:` lines at the **end**. No preamble,
no greeting, no closing remarks.

### A note on the field labels

`根拠:`, `条件:`, `残債:`, `判定:`, and `確信度:` are **wire tokens, not prose**. Deterministic code
parses them — the anchor sensor, `extract_anchors`, the orchestrator's checker, and golden
outputs in the selftest all match those exact strings. Do not translate them. Changing a label
is a change to the review pipeline's behaviour, and it belongs in a change that migrates the
parsers and their goldens together, not in a pass over the surrounding prose. Everything around
them is documentation and reads in whatever language the reviewer writes.

### Form

```
根拠:
1. (first ground — `path/to/file.ts:42`)
2. (second ground — `path/to/other.py:10-18`)
3. (third ground — a short quotation of the passage)

条件:
【マージ前必須】
- (what must be addressed before merge; omit the block if there is none)
【フォローアップ可】
- (what can follow later; omit the block if there is none)

残債:
- (debt or concern noticed outside this task; omit if there is none)

判定: <APPROVE|REJECT|APPROVE_WITH_CONDITIONS>
確信度: <高|中|低>
```

### Rules

- **Write the grounds first** (evidence-first). Anchored grounds always precede the verdict. Never
  open with the verdict.
- **Always end with the verdict** (a line beginning `判定:`, the first of the final two lines). You
  may quote another verdict line inside the body of your grounds; extraction takes the **last**
  `判定:` line.
- The verdict word is one of `APPROVE`, `REJECT`, `APPROVE_WITH_CONDITIONS` — the same vocabulary
  and the same meanings as before.
- **Always end with the confidence** (a line beginning `確信度:`, directly after `判定:`). `高` means
  you confirmed the evidence directly; `中` means circumstantial or indirect confirmation; `低`
  includes not having enough information.
- **`REJECT` at `確信度: 低` is forbidden** (false-positive control). Send a low-confidence concern
  to a condition under `APPROVE_WITH_CONDITIONS` or to debt, and say **not enough information**
  rather than deciding by guess.
- Exactly **three** grounds. Never more, never fewer.
- **Each ground carries an anchor that identifies its subject uniquely** — `file:line` (a range is
  fine) for code, a short quotation for prose or a record. An impression or a generality you
  cannot anchor is not a ground. Anchors do not count against the length limit.
- Split conditions into "required before merge" and "follow-up" as bullet lists. Omit either block
  when it is empty.
- Each sub-block (`【マージ前必須】`, `【フォローアップ可】`) is independently optional: do not print the
  header of a sub-block with nothing under it. When both are empty, omit the whole `条件:` block.
- Record under `残債:` only what you noticed outside this task's scope. Omit it when there is none.
- **120–250 words in total** (200–400 characters when writing in Japanese). No padding, no
  impressions, no sign-off.
