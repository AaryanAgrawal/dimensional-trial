# CLAUDE.md — Dimensional FDE trial

Working notes for whoever — human or agent — touches this repo next. **Read `WORKSPACE.md` in
full before doing anything else** — start at its "Cold start" section if this machine is new,
otherwise its "Next actions." It has the actual state, plan, and history; this file is just how to
work. Update `WORKSPACE.md` + push after every meaningful step — never leave a session's findings
only in chat.

## Layout

This repo folder is the root. `dimos` is cloned inside it (gitignored, its own git repo — never
tracked as content here). Every session, including work inside `dimos/`, starts from this root and
follows this file + `WORKSPACE.md`.

## Anchor on truth

Every claim is a hypothesis until the code or a run confirms it — no matter who made it: Aaryan,
the Dimensional team, the docs, a label on the robot, this file. Nobody is lying; they're
remembering, and systems drift out from under what people remember. Three real cases from this
trial, all found by checking:

- The robot's printed label says `192.168.10.190`. Its actual IP was `10.0.0.104` (port sweep).
- `relocalization.md` says fitness threshold 0.6. The code default in `module.py` is 0.45.
- "We already have reloc evals" — true, but it named an offline self-consistency metric
  (TOTAL_SPREAD), not a live benchmark. Broad claims compress; read the code to learn what a
  claim actually points at.

So: **a fresh run beats the code, the code beats the docs, the docs beat what anyone said.**
When a claim matters, execute it before building on it. When the evidence disagrees with a person,
say so plainly — agreement is not a deliverable, and an echo helps no one. "I don't know" and
"unverified" are complete answers; the only wrong answer is a confident guess.

## How to work (Karpathy's four)

1. **Think before coding.** State your assumptions explicitly. If uncertain, ask. Ambiguous
   request → name the interpretations instead of silently picking one; never hide confusion.
2. **Simplicity first.** The minimum code that solves the stated problem — no speculative
   abstraction, no unrequested flexibility, no second-caller interfaces for callers that don't
   exist. The test: would a senior engineer call it overcomplicated? Then simplify. Delete before
   you add; merge before you split — this repo is proof: three `.md` files, not fifteen.
3. **Surgical changes.** Touch only what you must; clean up only your own mess. Match existing
   style over personal preference. Notice something else that should change? Say so — don't fix
   it in the same diff.
4. **Goal-driven execution.** Convert the task into a verifiable success criterion before
   starting — a command, a test, a number. "Make it work" is not a goal; "this test passes" is.
   Verify by execution, not by reading code: sim → replay → hardware, in that order — each rung
   proves strictly more than the last. A claim with no run behind it is a guess; say so instead
   of dressing it up.

## Robotics non-negotiables

- **Every pose names its frame. Every number names its unit.** `world_T_camera`, never bare
  `pose`. Meters and radians (SI), always — a silent `deg`-vs-`rad` mismatch is how a robot
  drives into a wall.
- **No estimate ships without a health signal.** Reprojection error, fitness score, ambiguity
  ratio, sighting count. A pose with nothing attached telling you how much to trust it isn't a
  result — it's a liability with a decimal point.
- **SIMULATED, in the sentence itself, not a footnote** — every synthetic/rendered-pixel number.
  Someone skimming fast should never mistake a demo result for a hardware one.
- **Deterministic seeds, printed in the output**, and log everything a rerun would need — inputs,
  seed, exact command, git rev. A number nobody can reproduce isn't a number.

## Two-window sync (this repo lives on two machines + cloud sessions, at once)

- **`git pull --rebase` before touching anything.** Someone else may have pushed since you last
  looked.
- **Commit + push immediately after every real update** — small frequent commits, never one
  giant one at the end. A change that isn't pushed doesn't exist for the other window.
- **Conflicts: keep both intents.** Never resolve one by silently dropping the other side's edit —
  merge the meaning, not just the diff.
- **Never commit credentials, tokens, or personal/financial matter.** `credentials.md` is
  gitignored on purpose. When in doubt, leave it out and mention it instead of committing it.
- **Machine roles:** window 1 (laptop) owns the `dimos` branch/PR + the live robot; window 2
  (CUDA machine) owns offline eval/compute; both own `WORKSPACE.md` via pull-rebase. Don't touch
  the other window's lane — see `WORKSPACE.md` §2 for what each owns right now.
- **Tasks live in `WORKSPACE.md`**, as checkboxes with an owner + status, not in chat. To take
  one: mark it `[~] doing — <window>` and push immediately — the push *is* the claim. When
  finished: `[x] done — <one-line result>` and push. Never work a task another window has already
  marked `doing`.

## When stuck

State the assumption, name the simpler path, ask only if it's a real fork in the road — otherwise
proceed. This is one FDE's working repo, not a product: optimize for "the next person can pick
this up cold," never for cleverness.
