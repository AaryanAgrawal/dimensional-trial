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

## Test real dimos — never a re-implementation (Aaryan, Jul 20; the hardest lesson so far)

Every test, benchmark, or eval must exercise the **actual dimos pipeline** — `dimos --replay run
<blueprint>`, the real modules, the real data path — never a harness that rebuilds a dimos step
its own way. The instant a benchmark re-implements a data-path step (submap building, frame
re-anchoring, candidate generation, pose composition), it becomes a *different program* that
silently drifts from production and measures itself, not dimos.

Proof, and why this rule exists: our offline harness re-anchored the relocalization submap to the
**full body frame** (`prep.py`, `world_pts @ inv(P)`) — a step dimos never does (production builds
the submap in the LIO's gravity-aligned **world** frame via the real `VoxelGridMapper`). On the
tilted mid360 rig that manufactured a 52° tilt and a phantom "gravity-gate bug" that does not exist
in production. A full night went into "fixing" a bug in our own test scaffolding. Testing a
re-implementation tests the re-implementation.

So: the harness may **orchestrate** (pick recordings, hold PGO/referee-tag truth, score, analyze)
and may call real dimos for the legitimate steps (premap = `dimos map global --pgo --export`), but
it must **grade fixes the real pipeline published**, never fixes it computed itself. If you cannot
test something through real dimos, say so — do not substitute a look-alike.

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
- **Machine roles:** current ownership lives in `WORKSPACE.md` §2/§3 — read it before working;
  don't touch a lane another window has claimed. Both windows own `WORKSPACE.md` via pull-rebase.
- **Tasks live in `WORKSPACE.md`**, as checkboxes with an owner + status, not in chat. To take
  one: mark it `[~] doing — <window>` and push immediately — the push *is* the claim. When
  finished: `[x] done — <one-line result>` and push. Never work a task another window has already
  marked `doing`.

## Workflows (Aaryan, Jul 17)

**Always use ultracode workflows for everything substantive** — recon sweeps, design, writing
deliverable copy, verification of results, code review. Solo tool-calls are for trivial mechanical
steps and orchestration glue only. Findings from workflow agents get adversarially verified before
they land on the board (the night of Jul 17 is the template: every headline number independently
re-derived, two claims reframed by the verifiers).

## Writing code the dimos way (studied from lesh + Sam Bull, Jul 17)

Counted at dimos upstream/main 29f3555: lesh corpus 133 files / 21.0k code lines, Sam 2,221
blamed lines. File paths relative to the `dimos` package. Our-branch audit + TODOs at the end.

**Comments are WHY — physics and races, never narration.** Volume is ordinary (lesh 13.2%
comment/code vs repo 15.4%; Sam 6.1%); content is the differentiator: unit conversions and
failure mechanisms sit on the number, e.g. Pose3 noise "rad² vs m²" (pgo_auto.py:738-745):

    trans_var = max(0.005, float(pair.score))  # >= sigma_trans ~7 cm

Algorithm code comments like lesh (physics paragraph per block; module docstring opens with a
typed ASCII pipeline, pgo_auto.py:24-29). Infra comments like Sam (sparse, but 3-5-line
race/ordering paragraphs, ipc_factory.py:361-370). Freeform reST prose — Google Args:/Returns:
only in legacy files (15/133). Zero TODO/FIXME/HACK lands (0 in Sam's 2,221 lines).

**Raise loudly, catch narrowly, recover via sentinel.** lesh 10.4 raise vs 6.0 try per kloc;
zero bare `except:` in either corpus. `except Exception` only at CLI/module boundaries (17 of
lesh's 82 handlers; Sam exactly 1, logged). Inside algorithms crash via ValueError/TypeError;
at a boundary convert a *specific* exception to an in-band sentinel with a why-comment
(RuntimeError → `(identity, inf)` in _icp, pgo_auto.py:829-847). Messages give got-vs-want or
the caller's next step (memory2/stream.py:472-475):

    raise TypeError(".to_list() on a live stream would block forever. "
                    "Use .drain() or .save(target) instead.")

Asserts: near-zero in-process (lesh 1.8/kloc — raise instead); assert WITH diagnostic message
only for cross-process layout contracts (ipc_factory.py:371-374, Sam).

**Typing total; every escape scoped.** lesh 80–83% annotated, Sam 100%; all ~75 `type: ignore`
across both carry [error-codes] — zero bare, zero noqa. Tunables = pydantic config with
per-field unit comments + platform notes (pgo.py:97-99); value types = frozen @dataclass
(lesh; Sam pydantic-only). Verdict: pydantic for module configs, dataclass for geometry values.

**Small functions, one deliberate monster.** lesh median 7 lines (n=1,752, p90 25); Sam median
11. The sanctioned exception concentrates a tuning surface instead of abstracting it:
_search_for_loops, 160 lines / 11 keyword knobs (pgo_auto.py:573-587).

**Units in names, frames on poses.** Counted (lesh runtime): _ts 90, _trans 55, _bytes 49,
_thresh 38, _rad 14, _deg 10; frame_id x103; Sam: ts_ns, _frame_nbytes, cap_ms. Constants
UPPER_SNAKE with layout comment: `_HEADER_FIELDS = 3  # (seq, ts, length) per slot, all
int64` (ipc_factory.py:332).

**Logging sparse, structured, pre-rounded.** lesh 0.62 calls/kloc, Sam 6.3; zero f-string logs
in either. structlog event + kwargs: `logger.info("loop fallback fired", cur_idx=cur_idx,
drift=round(drift, 2), ...)` (pgo_auto.py:655-660). Warnings carry operator remediation
("increase slots or poll faster", ipc_factory.py:485). CLIs print/rich, they don't log.

**Tests are the docs** (32% of lesh's output, 601 tests). Geometry: seeded
`np.random.default_rng` synthetic scenes with construction rationale (test_pgo.py:133-146),
exact-literal asserts. Concurrency/IPC: Sam's invariant-per-test — the docstring names the
property ("Every message published within one ring is delivered exactly once, in order",
test_ipc_factory.py:70), try/finally cleanup, wait_until never sleep. Both meta-test their
conventions so fixtures can't rot (test_pgo.py:50-60; test_all_blueprints.py:98-101).

**Imports & commits.** Three ruff-enforced import blocks; heavy imports deferred with a
justification + enforcing-test pointer ("stays fast. See test_cli_startup.py", map.py:27-29).
Commits: terse imperative subjects, 86% body-less (Sam, mean 34.7 chars) — PR carries context.

**Our branch audit** (feat/fiducial-relocalization: 926 runtime / 770 test code lines) —
complies: typing 100%, 9 coded ignores; raise 10.8/kloc; boundary-only broad catch; seeded
SIMULATED scenes; unit-suffixed names (marker_length_m, _gravity_tilt_deg). Drift TODOs:
- [ ] 6/8 logger calls are f-strings (relocalization/module.py:129,139,184,208;
      fiducial/visual_relocalization_module.py:100,107). setup_logger IS structlog with a kv
      renderer (utils/logging_config.py:229) — switch to event + kwargs.
- [ ] bare `assert self._premap is not None` (relocalization/module.py:160) — message or raise.
- [ ] `MIN_WALL_POINTS = 100` (relocalization/relocalize.py:44) is naked while its neighbors
      carry why/unit comments (:35-38).

**Citations in code (Aaryan, Jul 20):** cite only *non-obvious* algorithms and tuned/"magic"
thresholds, with a simple URL on the same line — e.g. Huber IRLS, Markley quaternion mean, IPPE
mirror-ambiguity, Umeyama alignment, Open3D `segment_plane`. **Do NOT cite industry-standard
technique** — no FAST-LIO / Point-LIO / ICP / RANSAC / PGO / solvePnP references; that's assumed
knowledge and clutters the code. Rule of thumb: if a reader would need the paper to understand
*why this specific method/number*, cite it; if it's a household SLAM/vision term, don't.

**Checklist before pushing dimos code:**
1. Magic numbers: unit or physical translation on the same line; non-obvious algos/thresholds
   get a simple-URL citation (Huber/Markley/IPPE/Umeyama/segment_plane), never industry-standard.
2. Quantities carry units in the identifier; every pose names its frame.
3. No bare `except:`; `except Exception` only at a boundary, and logged.
4. Error messages: got-vs-want (`"got {n}"`, memory2/transform.py:115) or the caller's next step.
5. Every def annotated; every `type: ignore` carries an [error-code].
6. Logs: event string + kwargs, values pre-rounded, no f-strings; CLIs print instead.
7. Function > ~25 lines (lesh p90) and not the designated knob-concentrator → split it.
8. Tests: seeded rng, docstring states the invariant, wait_until not sleep, try/finally cleanup.
9. Comments answer WHY; no TODO/FIXME reaches main — fix it now or file it.

## When stuck

State the assumption, name the simpler path, ask only if it's a real fork in the road — otherwise
proceed. This is one FDE's working repo, not a product: optimize for "the next person can pick
this up cold," never for cleverness.
