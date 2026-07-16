# CLAUDE.md — Dimensional FDE trial

Working notes for whoever — human or agent — touches this repo next. **Read `WORKSPACE.md` in
full before doing anything else** — start at its "Cold start" section if this machine is new,
otherwise its "Next actions." It has the actual state, plan, and history; this file is just how to
work. Follow the sync protocol below, and update `WORKSPACE.md` + push after every meaningful step
— never leave a session's findings only in chat.

## The four rules

1. **Verify by execution, not by reading code.** Sim → replay → hardware, in that order — each
   rung proves strictly more than the last, and each rung's limitation is why the next one exists.
   A claim with no run behind it is a guess. Say so instead of dressing it up.
2. **Every pose names its frame. Every number names its unit.** `world_T_camera`, never bare
   `pose`. Meters and radians (SI), always — a silent `deg`-vs-`rad` mismatch is how a robot
   drives into a wall.
3. **No estimate ships without a health signal.** Reprojection error, fitness score, ambiguity
   ratio, sighting count. A pose with nothing attached telling you how much to trust it isn't a
   result — it's a liability with a decimal point.
4. **Delete before you add.** New file only if nothing existing can carry the thought. Merge
   before you split. Fewer, thicker docs beat many thin ones — this repo is proof: two `.md`
   files, not fifteen.

## Labeling

- **SIMULATED, in the sentence itself, not a footnote** — every synthetic/rendered-pixel number.
  Someone skimming fast should never mistake a demo result for a hardware one.
- **Deterministic seeds, printed in the output** — `--seed 42` or whatever. A number nobody can
  reproduce isn't a number.
- **Log everything a rerun would need** — inputs, seed, exact command, git rev if it matters.
  Future-you, or the next agent, starts with none of today's context.

## Diffs

- Surgical. Touch only what the task needs. Notice something else that should change? Say so —
  don't fix it in the same diff.
- Match existing style over personal preference.
- No speculative abstraction. One caller, one function — don't build the interface for a second
  caller that doesn't exist yet.

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
