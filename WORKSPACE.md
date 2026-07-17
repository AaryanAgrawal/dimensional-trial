# Dimensional Trial — workspace

*Living doc — updated every session; both machines + cloud sessions read this first. Everything
that matters lives here as one thick doc, on purpose (see `CLAUDE.md` rule 4) — sections below,
not separate files. Any machine should be able to clone this repo and start working from this
file alone.*

This is Aaryan's working context for the Forward Deployed Engineer trial at **Dimensional**
(dimensionalOS/dimos — "the OS for robotics"). The actual code changes live in `dimos/`, a
separate git repo cloned inside this folder (see §0) — gitignored here, never tracked as content,
so this repo's CLAUDE.md and docs govern dimos work too.

---

## 0. Cold start — new machine

Run these in order. If this machine already has both repos cloned and `dimos` synced, skip to §2
Next actions.

1. **Clone this repo** (if not already here):
   ```bash
   git clone https://github.com/AaryanAgrawal/dimensional-trial.git
   cd dimensional-trial
   ```
2. **Clone `dimos` inside this repo root**, on Aaryan's fork, on the trial branch (run from the
   repo root — `dimos/` is gitignored here, its own git repo, never tracked as content; nesting it
   is what makes this file and `CLAUDE.md` govern dimos work too):
   ```bash
   GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/dimensionalOS/dimos.git
   cd dimos
   git remote add fork https://github.com/AaryanAgrawal/dimos
   git fetch fork
   git checkout feat/fiducial-relocalization
   ```
   `GIT_LFS_SKIP_SMUDGE=1` skips pulling every LFS asset (recordings, weights) at clone time — the
   repo is otherwise ~3GB. Pull specific LFS data on demand later: `git lfs pull --include=<path>`.
3. **Sync the environment** (from inside `dimos/`):
   ```bash
   uv sync --all-groups
   ```
4. **CUDA note:** map ops (feature matching, graph refinement, village-scale eval) benefit hugely
   from a CUDA-capable box — if this machine has one, pass `--device CUDA:0` to `dimos map global`
   commands (§6 CUDA-machine commands); if not (e.g. the dev Mac), `--device CPU:0` works, just
   slower. Confirm which this machine is before running anything compute-heavy.
5. **Robot connection crib** (only if this machine will drive the live Go2 — skip if this is the
   CUDA/offline-eval machine):
   - Robot: Unitree Go2 **"greenwald"** (`dim-0190056`). Power on via its side button, wait for it
     to finish booting and stand up on its own (~30-60s) — that's the robot ready signal.
   - It joins the office WiFi automatically; last known IP `10.0.0.104` (the physical asset tag
     printed on the robot, `192.168.10.190`, is **stale** — ignore it, DHCP has moved it since).
   - If `10.0.0.104` doesn't respond, re-sweep the office subnet for the WebRTC control port
     (9991):
     ```bash
     nmap -p 9991 --open 10.0.0.0/24   # adjust the subnet if the office network differs
     ```
   - Smoke-test the stack **without** the real robot first (replay path, always available, zero
     hardware risk):
     ```bash
     cd dimos
     uv run dimos --replay --replay-db=go2_bigoffice run unitree-go2-visual-relocalization
     ```
     Expect: 11 modules deployed, `color_image` autoconnected, zero tracebacks, zero `world->map`
     corrections (the replay recording has no tags in view — that's correct, not a bug). Foreground
     only — `--daemon` panics on this Mac after the fork (Zenoh's I/O driver doesn't survive it);
     if you see orphaned worker processes, that's why.
   - Once the smoke test is clean, connect to the real robot:
     ```bash
     ROBOT_IP=10.0.0.104 dimos run unitree-go2-visual-relocalization
     ```
6. **Read the rest of this file**, then continue from §2 Next actions.

## 1. Where everything lives

| What | Where |
|---|---|
| The module + all code changes | `dimos` (cloned inside this repo root, §0) — **ONE working branch: `feat/fiducial-relocalization`** (Aaryan's naming, Jul 17) — all 16 commits (PR #2808 work + the verified priors system), ON THE FORK (pushed + renamed Jul 17). PR #2808 is currently CLOSED by the rename mishap — see §3 for restore options; **dimos-remote is FROZEN until the work is verified** (§2 CUDA queue). All new dimos-side work lands on this one branch. |
| The PR | **#2808** — https://github.com/dimensionalOS/dimos/pull/2808 |
| The public presentation page | https://aaryanagrawal.me/dimensional |
| Trial page source | github.com/AaryanAgrawal/portfolio → `src/app/dimensional/` (deploys to aaryanagrawal.me/dimensional via `vercel --prod` from that repo's checkout) |
| This repo | context/plan/benchmark-protocol/history only — `dimos` sits inside this folder on disk but is its own git repo, gitignored, never tracked as content here |
| Benchmark instruments (logger, bench runner, referee, overlay, survey dumper) | `trial/scripts/` in this repo |
| Synthetic proof harness (real detector, rendered pixels, no hardware) | `demo/` in this repo — `cd demo && ./run.sh` |
| Physical marker kit (printable tags, surveyed map) | `print/*.pdf`, `office_markers.yaml`, this repo |
| Real benchmark run output | `trial/results/` and `trial/scripts/out/` — generated, untracked; EXCEPT `trial/results/figures/*.png` (comparison graphs — tracked, shared between machines) |
| Everything else from the trial (spec docs, research notes, day-by-day roadmap, PR drafts, page copy) | local disk only, untracked — folded into this doc's sections below where still load-bearing |

## 2. Next actions

- [~] doing — **Phase 1: universal confidence reading**: BUILT + adversarially verified on
      branch **`feat/fiducial-relocalization`** (the ONE branch for all dimos trial work; 3 new
      commits on top of the 13 PR commits, HEAD `a6be7e42e`, pushed to the fork Jul 17).
      What landed: `relocalize.py` split into `generate_ransac_candidates()` + `refine_candidates()`
      (public `relocalize()` preserved — parity proven bit-identical against the actual pre-refactor
      code, same seeded scene); new `priors.py` (`Candidate` = T + source + confidence tier;
      `RelocPrior` protocol; `RansacPrior`; `LastPosePrior`; `relocalize_with_priors()` → (T,
      fitness, winning_source)); `module.py` gains `use_last_pose_seed` config (default off = today's
      behavior) + `source=` in the accept log. No source bypasses the judge — confidence never
      overrides fitness (tested). Tests: 6/6 new suite green; fiducial suite 65/66 with the 1
      failure proven pre-existing at the base commit (LCM-timing flake). Remaining for Phase 1
      close: decide push/PR form with the team context — see §4 v5.
- [ ] todo — **Phase 2: fiducial prior**: plug PR #2808's visual `world→map` estimate in as a
      high-confidence-tier prior into that same judge — same universal reading, no bypass — after
      Phase 1 lands.
- [ ] todo — **Phase 3: sections-harness testing**: cut sections of the recorded PGO maps
      (villages, go2_hongkong_office) and relocalize them against the PGO premap, WITH MARKERS —
      truth = PGO-corrected poses (silver truth) + marker agreement — the CUDA queue below (task 1)
      is its existing-stack baseline half, as already written.
- [ ] todo — post the Linear ticket — **Aaryan, today (Jul 17)**; §5 draft is v5-ready, and §3
      has the carry-forward set (implements DIM-940; adopt DIM-920's acceptance test; relate
      DIM-944 + END-76)
- [ ] todo — CUDA runs: villages 1-5 + go2_hongkong_office at full scale (see "Tasks — CUDA
      machine" immediately below — claim from there, not from this line)
- [ ] todo — website update to v5 — deferred (Aaryan Jul 16), page still shows the old benchmark
      framing
- [ ] todo — page comments feature — on hold, not blocking, revisit after the above

### Tasks — CUDA machine (window 2) — committed first work package (Aaryan, Jul 16)

GOAL: measure how well the EXISTING stack works, with numbers AND visual/graphical proof — raw
odometry vs. PGO (marker scatter, replay), and RelocalizationModule finding itself on a premap —
before any new code. Truth model (Aaryan, Jul 16): **PGO-corrected poses = silver truth for
replay work** (best available reference; still the same lidar data optimized, so it can share
failure modes with what it grades — markers add independent anchoring). Live-robot work later is
demos/spot-checks, not a benchmark — the real-life benchmark is cut. Note these runs are **replay
of real recorded drives** (real sensor data), not simulation — label results "replay," never
"SIMULATED."

- [ ] 0. Sanity: confirm the map pipeline runs on CUDA — quick `dimos map global hk_village3 --markers --no-gui`; should be much faster than CPU (~3 min on the Mac). Fix device selection if not.
- [ ] 1. Walk docs/capabilities/navigation/relocalization.md end-to-end on a village recording: build the loop-closed global map, `--export` a premap, then run relocalization against it in replay — watch RelocalizationModule find itself on the map. **Capture the run log** (fitness, time_cost, accepted vs rejected counts, warmup skips) — it feeds the graphs in task 6.
- [ ] 2. Odometry test: `dimos map global hk_village4 --markers --no-gui` (map + marker poses from raw odometry). Save the `.rrd`.
- [ ] 3. Comparison: `dimos map global hk_village4 --pgo --markers --no-gui` — compare marker agreement vs task 2 (avg distance between repeated marker sightings). Save the `.rrd`.
- [ ] 4. Full offline baseline table: repeat 2+3 across hk_village1..5 + `uv run python -m dimos.mapping.loop_closure.eval <village>` per village → per-village TOTAL_SPREAD + raw-vs-PGO marker RMS.
- [ ] 5. The big eval map: `dimos map global go2_hongkong_office --pgo --markers --no-gui` + its numbers.
- [ ] 6. **Graphs — the visual/graphical proof comparing both (required deliverable, Aaryan):**
      (a) per-village grouped bar chart: marker scatter raw-odom vs PGO, side by side;
      (b) per-marker top-down scatter plot: the same physical marker's repeated sighting
      positions, raw vs PGO overlaid on shared axes — the drift made visible;
      (c) from task 1's log: fitness-over-time and correction-magnitude-over-time series,
      accepted vs rejected attempts marked;
      (d) side-by-side `.rrd` screenshots of the raw vs PGO marker overlays.
      Matplotlib, exact command + git rev printed on/beside each figure, PNGs committed to
      `trial/results/figures/` in this repo (tracked — see .gitignore) + pushed.
- [ ] 7. Write all numbers/tables into §7 Findings, note anything that behaved differently than documented, push.
- [ ] 8. **Build + run the fiducial sections harness (Phase 3 method, §6):** cut sections/frames
      from the recorded drives (villages + go2_hongkong_office), relocalize each against the
      PGO-exported premap, score vs PGO-corrected pose (silver truth) AND marker agreement
      (fiducials = independent anchoring) — #2137's `run.py`/60-frame pattern is the template
      (fetch branch `sloptimization/ransac` to read it). **Harness code lives in THIS repo**
      (`trial/harness/`), importing dimos as a library from the checkout — never in dimos itself
      (the dimos branch is single-writer, window 1 — Aaryan's one-branch rule). Report:
      success-rate table (1m/15° bar), per-section error vs PGO truth, marker-agreement numbers,
      figures into `trial/results/figures/`, all of it into §7. Deterministic seeds, exact
      commands logged.

**Expected numbers (priors, each with its source — the runs verify or refute; results replace
these):**
- Raw-odom marker scatter: meters-scale over a multi-minute loop (drift is unbounded/super-linear;
  village3 Mac reference: TOTAL_SPREAD 4.955, raw-vs-PGO marker RMS 0.540/0.577 m, n=4).
- PGO marker scatter: sub-meter, order tens of cm (village3 RMS ~0.5 m level).
- Reloc-in-replay (task 1): skips until the live submap reaches 50k points; each solve 3–8 s
  (capability doc's own logs); accepted fixes at fitness ≈0.45–0.7 (doc logs show 0.657–0.684
  accepted); converged correction magnitudes cm-to-tens-of-cm (doc logs `published_t`
  ≈0.06–0.17 m). Global-search success prior: ~90% within 1 m/15° on go2_hongkong_office
  (#2137's tuned pipeline, 60-frame harness) — may be lower on villages.

Rules reminder inside the section (one line): claim a task by marking `[~] doing — window 2` + push (the push is the claim); finish with `[x] + one-line result`; do not touch the robot or push to the dimos fork/PR from window 2.

## 3. Current state (2026-07-17 — READ THIS FIRST on the CUDA machine)

**Aaryan is now driving from window 2 (the CUDA machine).** Window 1 (laptop) is standby.

**Code state — ONE branch, one intended PR:**
- Fork branch **`feat/fiducial-relocalization`** (renamed from `feat/marker-localization-core`)
  carries ALL 16 commits: the 13 reviewed PR commits + 3 new, adversarially verified commits
  (Phase 1 priors/universal-confidence system — see §2 Phase 1 item for exactly what landed).
- **PR #2808 is currently CLOSED — by mistake.** The branch rename orphaned the PR's head ref:
  GitHub auto-retargets renames only for same-repo PRs, NOT cross-repo (fork→upstream) PRs.
  Lesson recorded. Restore options when ready: (a) rename the fork branch back + reopen #2808
  (most likely restores the full review thread; the 3 new commits are already on the branch),
  (b) try reopening as-is, (c) fresh PR from `feat/fiducial-relocalization` (clean name, loses the
  #2808 review thread).
- **REMOTE FREEZE (Aaryan, Jul 17): no dimos-remote mutations of any kind** — no pushes, no
  PR reopen/create, no renames — **until the work is verified** ("we will only do it once
  things are verified"). Verification = the §2 CUDA queue (tasks 0–8). This trial repo's `main`
  is the one exception (Aaryan: "put it all in" — the coordination channel stays live).

**Plan-of-record v5** (Aaryan, Jul 16): Phase 1 universal confidence reading via a shared judge
over pluggable priors — BUILT + verified, local+fork branch `feat/fiducial-relocalization`; Phase 2
fiducial module as a high-confidence prior into the same judge; Phase 3 offline testing on
sections of the recorded PGO maps with markers (§6). Real-life benchmark cut.

**Linear (Dimensional's tracker) — how the trial organizes from here (Aaryan, Jul 17):**
- Auth: Aaryan's API key is in the laptop's `credentials.md` (gitignored) — on the CUDA machine,
  paste it into a local `credentials.md` there (NEVER commit it; `.gitignore` already covers it).
  Verified working: acts as aaryan@dimensionalos.com, org `dimensional`, teams incl. DIM
  (Engineering). API: POST https://api.linear.app/graphql, header `Authorization: <key>`.
- **Existing issues to carry forward (searched Jul 17 — do NOT open a duplicate):**
  - **DIM-940 [Backlog] "Pluggable Relocalization Heuristics"** (lesh, from GitHub #2209) — IS
    Phase 1+2: "aruco tags are actual poses with high confidence... should run somewhere in
    relocalization function." Our priors system implements this issue.
  - **DIM-920 [In Review] "AprilTag relocalization"** (Dan & Ivan's parallel track) — carries a
    ready-made acceptance test: "In a room with 2 known AprilTags, robot publishes correct
    map→world TF within 5s of seeing a single tag." Adopt as Phase 2's acceptance bar; their
    building blocks (#2107 detector, #2044 recordings) are what our module already consumes.
  - **DIM-944 [Backlog] "Relocalization tuning system, wrap autoresearch toolkit"** (lesh) — IS
    Phase 3/4: deterministic autoresearch eval (pegged seeds, process-per-core), "chainable
    toolset with tunable hyperparams per step," AND lesh's own circularity warning: never
    evaluate relocalization on the dataset the target map was built from — same rule as ours.
  - **END-76 [Backlog, Product] "Re-localization"** — the index issue (links #2160 merged,
    #2143 spec OPEN, #2107, #2044).
- **Recommended ticket (Aaryan posts, his call):** ONE issue in DIM titled
  "Relocalization: universal confidence reading + fiducial prior (FDE trial)" — body = §5 draft;
  set relations: implements DIM-940, related DIM-920 (adopt its acceptance test) + DIM-944
  (Phase 3/4) + END-76; add the PR link once the PR is restored. Alternative (leaner): skip a
  new issue — comment the v5 plan on DIM-940 and claim it.

## 4. Plan of record (v5)

Direction as of Jul 16 (Aaryan, latest burst — supersedes the same-day v4 draft, which itself
superseded v3): the real-life benchmark is gone. Three phases, then the fusion end state:

- **Phase 1 — Universal confidence reading (now, in progress on branch `feat/reloc-priors`).**
  Every relocalization answer carries ONE comparable confidence measure, regardless of method.
  Mechanism: pluggable priors propose candidates — RANSAC (today's SCALE_PLAN loop) is the first
  prior, feeding fine ICP; a last-accepted-pose seed is the cheap second — and ALL candidates are
  scored by the same shared judge (wall-only fine-fitness at RERANK_DIST). That score IS the
  universal confidence, published with the answer + winning source. This is lesh's own #2137
  next-step ("we need a match confidence measure").
- **Phase 2 — Fiducial as prior.** The visual module's `world→map` estimate (PR #2808) proposes
  into the same judge as a high-confidence-tier prior — same universal reading, no bypass.
- **Phase 3 — Testing, offline, dimos-native.** Cut SECTIONS of the recorded PGO maps (villages,
  go2_hongkong_office) and relocalize them against the PGO premap, WITH MARKERS — truth =
  PGO-corrected poses (silver truth) + marker agreement (independent anchoring). #2137's 60-frame
  harness is the exact pattern; rerun visualization for eyeballing. The real-life benchmark —
  start/end-tag referee, live routes, kidnap runs, pass bars, field battery — is cut (Aaryan,
  Jul 16). Live robot use later = demos/spot-checks, not a benchmark.

**End state: fusion.** Relocalization becomes a package, not a module — lidar-ICP and visual-tag
become **sources** under it (`dimos/mapping/relocalization/{base,lidar,visual,fusion}.py`); each
source emits a pose candidate + confidence + a health signal (lidar: ICP fitness; visual:
reprojection error + ambiguity ratio + tag count); `fusion.py` is the *only* `world→map` publisher
— one source active = pass-through, multiple active = gated priority + covariance weighting +
degradation ladder, never averaging two disagreeing corrections into a meaningless pose.
`RelocalizationModule` (today's public class) stays as a deprecation alias — nothing that imports
it today breaks. Trial discipline: build this **additively** (new files only — `base.py`,
`visual.py`, a `fusion.py` skeleton) — the lidar file itself is lesh's, not touched on this branch;
propose, don't restructure.

**Phase 4 candidate design (Jul 16 study, not yet agreed with the team).** Three-part split: global
search on demand (the existing `relocalize()`, fired at boot/kidnap/health-collapse instead of on a
timer) · cheap continuous tracking (ICP seeded by the last `world→map`, plus the marker prior when a
tag is visible — mostly re-plumbing of the pipeline's existing tail stage) · a health monitor that
owns `world→map` and decides which mode runs (inputs: fitness + trend, reprojection error,
ambiguity ratio, jump-implausibility gate, age-of-relocalization — mostly numbers the pipeline
already computes). Compute-aware: same architecture on every machine tier, only rate/budget config
differs. Industry-pattern claim verified against primary sources Jul 16 — see "Industry practice,
primary-sourced" in §7: largely confirmed, with two corrections (tracking is motion-gated or 2-8 Hz,
not 10-40 Hz; and production systems mostly HALT and ask on lost — auto-triggered global re-search
is beyond standard practice, not a copy of it).

**Phase 4 tuning = autoresearch (Aaryan, Jul 16 — part of the plan, not a maybe).** The
monitor/fusion knobs (fitness gates, mode-switch thresholds, hysteresis, jump-gate width,
per-tier rate/compute budgets) get tuned by an autoresearch loop in the exact #2137 pattern:
- **One modifiable file** — the monitor/fusion policy (thresholds + mode logic), nothing else.
- **A read-only harness** — the Phase 3 sections-of-PGO-maps harness (PGO-corrected pose silver
  truth + marker agreement), fixed time budget, deterministic seeds. This is the "better data —
  fiducial markers as ground truth" the #2137 next-steps section asked for.
- **A results ledger** (`results.tsv` style) with a strict metric hierarchy — error vs PGO silver
  truth, then marker-agreement spread, then compute cost — keep/discard per experiment, every row
  reproducible.
The agent grinds the knob space against the Phase 3 harness; humans review the ledger, not the
guesses.

**Superseded 2026-07-16 (Aaryan):** the v4 draft (same day) and the custom real-life benchmark —
start/end-tag referee, routes, kidnap runs, fairness rules, pass bars, field battery, benchmark
CLI plan — cut by Aaryan Jul 16; full text in git history ≤ e54ea3c; instruments remain in
`trial/scripts` + the branch, unplanned.

## 5. Linear ticket (draft, ready to post)

> **Title:** Relocalization — universal confidence reading + fiducial prior (trial project)
>
> **Context:** RelocalizationModule direction = method-agnostic pluggable priors feeding ICP (per
> review); today: FPFH+RANSAC+ICP global reloc (tuned via the #2137 autoresearch harness) + offline
> marker-ground-truth eval (`loop_closure/eval.py` marker-spread over hk_village recordings); this
> trial adds a universal confidence reading across all priors, then the highest-confidence prior
> (fiducial markers), then offline sections-based testing. The real-life benchmark is cut.
>
> **Phase 1 Universal confidence reading:** pluggable priors (RANSAC first, a last-accepted-pose
> seed second) propose candidates; all are scored by the same shared judge (wall-only fine-fitness
> at RERANK_DIST) — that score is the one comparable confidence published with every answer +
> winning source. Answers lesh's #2137 next-step ("we need a match confidence measure"). In
> progress on branch `feat/reloc-priors`.
>
> **Phase 2 Fiducial prior:** PR #2808's visual `world→map` estimate proposes into the same judge
> as a high-confidence-tier prior — same universal reading, no bypass; marker poses via stream
> (K/V in map global later).
>
> **Phase 3 Testing (offline, dimos-native):** cut sections/frames from the recorded PGO maps
> (villages, go2_hongkong_office), relocalize each against the PGO-exported premap, WITH MARKERS —
> score vs. PGO-corrected pose (silver truth) + marker agreement (independent anchoring). #2137's
> 60-frame harness is the template; rerun visualization for eyeballing.
>
> **Phase 4 Method manager + runtime degradation:** parallel/toggleable/confidence-weighted,
> compute-aware, "age of relocalization" — knobs tuned by autoresearch against the Phase 3 harness.
>
> **Deliverables:** universal confidence measure shipped across all priors · fiducial prior
> integrated · sections-harness results table · method-manager design (+impl if time).
>
> **Open questions:** semantic prior nudge-vs-parallel · stream vs K/V timing · amend #2808 vs
> follow-up · where results live.
>
> **Needs:** CUDA machine; review @lesh.

## 6. Testing (Phase 3 — offline, dimos-native)

**Superseded 2026-07-16 (Aaryan):** the custom real-life benchmark — Routes & modes, the
honest metrics set, the start/end-tag referee (`--holdout-tag`), Fairness rules, Pass bars, and
the 7-card field battery — is cut; full text in git history ≤ e54ea3c. The instruments it
produced (`trial/scripts/bench.py`, `holdout_overlay.py`, `metrics_logger.py`, `report.py`)
remain in `trial/scripts/` and on the branch, unplanned — not deleted, just not the testing method
going forward. Testing is now the sections harness below, offline, against PGO silver truth +
markers.

### The sections harness (Phase 3 method)

Cut sections/frames from the recorded drives (villages, go2_hongkong_office), relocalize each
against the PGO-exported premap, and score against PGO-corrected pose (silver truth) + marker
agreement (independent anchoring — a physical marker never moves, so tight clustering of its
repeated sightings is a truth signal that doesn't share PGO's own failure modes). #2137's
`run.py` / 60-frame harness is the exact template: one modifiable file, a read-only harness, a
results ledger, deterministic seeds. Rerun visualization (`.rrd`) for eyeballing, same as the
sim → replay ladder below.

### Relationship to the existing offline eval (don't conflate these two)

- **PR #2137** (`sloptimization/ransac`, lesh, open/unmerged) is **not** the marker-ground-truth
  benchmark — it's an autoresearch loop that tuned `relocalize.py` (FPFH+RANSAC+multi-scale+ICP
  global relocalization, no initial pose) against a fixed 60-frame harness built from
  **PGO-corrected poses**, not markers. This is the existing global-reloc RANSAC implementation
  `RelocalizationModule` calls today in production (merged via #2160).
- **The actual existing benchmark** is `dimos/mapping/loop_closure/eval.py`, already on `main`:
  PGO over the full lidar stream, AprilTag detection, groups sightings by `marker_id`, sums
  pairwise distance between every PGO-corrected sighting of the same marker (`TOTAL_SPREAD`) — a
  physical marker never moves, so tight clustering of its repeated sightings *is* the ground-truth
  signal. Self-consistency only — no raw/odom baseline, no external ground truth.
  ```bash
  uv run python -m dimos.mapping.loop_closure.eval             # all hk_village1..6
  uv run python -m dimos.mapping.loop_closure.eval hk_village3  # one recording
  ```
- **What eval.py does NOT cover** (the gap `dimos/mapping/benchmark/` targets instead): live
  on-robot return error vs. an independent held-out reference, kidnap recovery time, runtime/
  compute degradation on hardware, relocalization accuracy/success-rate (PR #2137's harness covers
  a narrow slice of this, one 60-frame set, one office recording).

### Testing without the robot — the sim → replay ladder

How the benchmark is exercised with no live robot (lesh, Jul 16: the existing recordings support
most of the development without the robot; his homework = walk relocalization.md end-to-end in
replay):

1. **Sim / unit** (done, this branch): synthetic tag renders through the real PnP path — the 33+
   tests, the envelope sweep; the ambiguity gate came out of this rung. Proves the math; proves
   nothing about real sensors.
2. **Replay** (the workhorse — the CUDA queue in §2 is exactly this): recorded drives
   (`hk_village1..5`, `go2_hongkong_office`, `go2_bigoffice`) played back through the REAL stack
   (`dimos --replay --replay-db=<name>`). All villages observe markers, so all three modes can run
   on identical input:
   - odom mode = replay with relocalization off;
   - lidar mode = replay + `RelocalizationModule` against an exported premap;
   - visual mode = replay + `VisualRelocalizationModule` against surveyed marker poses.
   Replay beats live for FAIRNESS: same drive, bit-identical sensor input, three processing
   configs — impossible live (you can never drive the same route twice). Offline scoring = marker
   agreement (TOTAL_SPREAD via `loop_closure.eval`) + raw-vs-PGO marker RMS.
   - **Kidnap in replay**: start relocalization with no initial pose against the premap
     mid-recording — the stolen-robot condition exactly (lesh's homework item 1 is this: "watch
     RelocalizationModule find itself on the map"); recovery time is measurable offline.
3. **Hardware** (the only rung that needs greenwald): the live start/end-tag referee closure (a
   replayed robot can't be commanded to return), the physical pick-up/place-down kidnap, compute/
   runtime degradation on the real onboard budget, new route variants.

Each rung proves strictly more than the last; every claim is labeled by the highest rung that
produced it (SIMULATED / replay / hardware) — CLAUDE.md rule 1.

### CUDA-machine commands

```bash
# full 6-village loop-closure self-consistency sweep (the existing benchmark, as-is)
uv run python -m dimos.mapping.loop_closure.eval

# per-village map build + visual marker overlay (raw vs PGO in one .rrd)
dimos map global hk_village1 --pgo --markers --device CUDA:0   # repeat for 2..6

# PR #2137's RANSAC-tuning harness (own data/ fixtures, not in main's tree —
# fetch branch `sloptimization/ransac` if resuming that tuning loop)
git fetch origin sloptimization/ransac && uv run dimos/mapping/relocalization/run.py

# Aaryan's own held-out-tag benchmark (this branch, `dimos benchmark run|report`)
dimos run unitree-go2-relocalization --daemon   # start the stack first
dimos benchmark run --mode visual --route <name> --marker-map <path>
```

## 7. Findings (tonight's hands-on results + verification)

- **Marker-pose storage, verified in-repo (Jul 16, answers lesh's K/V line):** upstream `main`
  already has a live marker-**detections** stream (`MarkerDetectionStreamModule`, #2278 —
  `Detection3DArray` per frame for LCM consumers) and `MarkerTfModule` mirroring detections into
  TF. What does NOT exist anywhere upstream: persisted **known** marker poses in the map frame.
  `map global --markers` computes PGO-corrected marker poses but only draws them into the `.rrd`
  visualization (`world/pgo_map/markers`); `--export` writes the point cloud only
  (`<dataset>.pc2.lcm`), no marker artifact; `loop_closure/eval.py` prints TOTAL_SPREAD and
  persists nothing; the replay marker scripts are self-described throwaways. So lesh's "should
  offer a K/V store" is a real gap — and this branch's `office_markers.yaml` + `load_marker_map`
  is exactly that missing artifact, done as a file (the K/V shape; a stream publisher can carry
  the same schema "for now" per his direction).

- **village3 run** (Mac, CPU:0, 102s recording, 702 lidar / 1432 color frames):
  `eval.py hk_village3` → **`TOTAL_SPREAD=4.955m`, `TOTAL_PGO_TIME=2.22s`**. Standalone
  raw-vs-PGO script on marker id=10's 4 sightings: RAW RMS **0.540m** / max **0.833m** / max-pairwise
  **1.438m** vs. PGO-corrected RMS **0.577m** / max **0.962m** / max-pairwise **1.370m** — roughly a
  wash at n=4, one short recording; needs bigger sets (all 6 villages) on CUDA to mean anything —
  once assigned via §2 "Tasks — CUDA machine".
- **Benchmark kit pre-drive verification** (fresh run, 2026-07-15 evening, macOS, robot offline):
  6/6 components PASS (start/end referee · survey dumper · in-repo benchmark module · marker
  localization · replay integration · bench lifecycle); 52/52 pytest tests passed; `mypy --strict`
  clean on 9 source files; 4/4 live-replay assertions. Zero failures. One diagnosis worth keeping:
  don't park the robot square-on to the holdout tag for a start/end check — a 20-30° oblique view
  measurably tightens the referee (near-frontal views leave PnP depth/tilt poorly conditioned on a
  100mm tag at close range). Reproduce: `cd dimos && uv run pytest ../trial/scripts/tests/`.
- **Demo harness** (`demo/`, synthetic, real detector on rendered pixels): nominal scenario
  `ate_rmse_raw_odom_m: 1.75 → ate_rmse_corrected_m: 0.26` (6.76x improvement, post ambiguity-gate;
  0.33m/5.33x pre-gate, the number cited in the original PR). Detection rate 60.4% of frames saw
  ≥1 tag. Reproduce: `cd demo && ./run.sh`, read `out/metrics.json`.
- **RelocalizationModule scheduling, verified in `module.py` (Jul 16):** runs unconditionally
  whenever a premap is configured — live accumulated map stream throttled to one attempt per 2s
  (`RELOC_INTERVAL`), skipped under 50k points, dropped while a previous attempt still computes
  (attempts take 3-8s per the capability doc's own logs). Every attempt is the FULL global search
  (multi-scale multi-restart FPFH+RANSAC → inlier re-rank → fine ICP) from scratch — it never seeds
  from its previous answer. Only gate is post-hoc fitness (code default 0.45; the capability doc's
  example logs show 0.6 — provenance of the difference unknown). No confidence trigger, no "am I
  lost?" check, no jump/consistency guard — any fitness-passing answer replaces the `world→map` TF.
  A separate 2s heartbeat republishes the last accepted TF. With no `map_file` the module logs
  "disabled": all correction in the stack today is premap-relative; the odometry trail
  (`world→base_link`) is never corrected by anything.
- **PR #2137 "Autoresearch on relocalization" — what it actually is, verified Jul 16** (leshy,
  opened 2026-05-17, still open, branch `sloptimization/ransac`; read from the PR body + `program.md`
  + `results.tsv`): "autoresearch" = an autonomous LLM experiment loop. `program.md` is the loop's
  instruction file: edit `relocalize.py` (only modifiable file) → run read-only harness `run.py` →
  log a row to `results.tsv` → keep/discard by strict metric hierarchy (success_rate, then
  median_distance, then total_seconds), deterministic per-frame seeds, explicit "NEVER STOP"
  autonomy clause, simplicity criterion. Harness: 60 kidnapped-robot test frames from
  `go2_hongkong_office` vs. PGO-corrected ground truth, success = within 1m AND 15°, 90s total
  budget. `results.tsv` ledger: success 25% → 91.7% (winning ideas: multi-restart RANSAC,
  fine-scale fitness re-rank, top-K ICP re-rank, wall-only scoring, FPFH caching). Residual ~8%
  failures documented as 180° yaw flips (corridor symmetry) and wrong-room matches (FPFH
  descriptors not globally unique) — the failure class a uniquely-identified marker removes.
  `program.md`'s own failure-modes list includes "high RANSAC fitness paired with low ICP fitness
  at fine scale" — their docs acknowledging fitness alone is an imperfect confidence measure.
  leshy's PR next-steps (paraphrased): better data from multiple independent loop-closed runs or
  fiducial markers as ground truth; a match-confidence measure that corresponds to ground-truth
  quality; a runtime-vs-number-of-points quality curve; open "continuous alignment" questions
  (always search for a better match? can the mapper change its mind?). Timing caveat: ~1.1s/frame
  is wall-clock across ALL workstation CPU cores in parallel (single-core per-call cost is
  seconds; not an onboard number). leshy's comment on the PR: kept open as an artefact of work
  that's a good example for others, just shared with the candidate.
- **Industry practice, primary-sourced (Jul 16 — 5-lens research sweep: REP-105 text, Nav2/AMCL
  docs+source, AMR vendor manuals, SLAM papers, Spot GraphNav SDK docs):**
  - **Never rewrite odometry — universal.** REP-105 verbatim: the `odom` frame "is guaranteed to
    be continuous… without discrete jumps" and drifts "without any bounds"; the localizer "does
    not broadcast the transform from map to base_link… it broadcasts the transform from map to
    odom." dimos's `world→map` architecture matches the standard exactly.
  - **Tracking is motion-gated or 2-8 Hz, not 10-40 Hz** (corrects the from-knowledge figure).
    AMCL's expensive update fires only after the robot moves `update_min_d`=0.25m or
    `update_min_a`=0.2rad — scan-driven and motion-gated, no fixed rate. SICK NAV350/LiDAR-LOC:
    ~8 Hz. Spot GraphNav: "updates localization at least twice a second." Directly borrowable for
    dimos: today's module burns a full search every 2s even parked — a motion gate is a free win.
  - **On "lost," production systems mostly HALT and ask — automatic kidnap recovery is NOT
    standard** (corrects the from-knowledge claim). Nav2 ships `ReinitializeGlobalLocalization`
    as a primitive but wires it into ZERO default behavior trees (verified by grepping the shipped
    XML). MiR: lost → error after timeout, operator re-places the position. Omron: manual
    re-localize via UI; health signal = localization score (<70% → act). Spot GraphNav: maintains
    a lost/stuck assessment and "will refuse to navigate autonomously" when lost — fix is
    operator/re-record. So: health *signals* are ubiquitous; auto-triggered re-search is beyond
    standard practice (a design choice, not a copy) — and a v1 monitor that just declares lost +
    stops trusting is itself industry-standard behavior.
  - **Fiducials in production — solid precedent, two grades.** SICK NAV350: reflectors ARE the
    primary continuous localization (≥3 visible, ~8 Hz, output = position + quality metric, the
    vehicle controller decides how much to correct). Spot GraphNav: AprilTags REQUIRED at every
    Autowalk mission start and in "feature deserts," opportunistic loop-closure anchors otherwise.
    Locus: barcode fiducials as fine anchors over SLAM. Go2's camera-based marker prior sits in a
    well-trodden lane.
  - **Live trajectory re-optimization is standard only in SLAM-grade systems** — Cartographer
    (background PGO "periodically optimized"), LIO-SAM (iSAM2 on node insertion), ORB-SLAM3 (BA
    in an independent thread) — all reconciling jumps with smoothness via the dual-frame/dual-graph
    split; FAST-LIO2 is the deliberate counter-example (odometry-only, no loop closure, by the
    authors' own description). Deferring live PGO while operating on premaps matches common
    practice.
- *(CUDA-machine window: once §2 "Tasks — CUDA machine" has an assigned task, append the
  resulting numbers here, plus anything that behaved differently than this doc predicted.)*

## 8. Runbook — day-of operational essentials

**Self-survey installer flow** (no tape measure, condensed from the execute-verified flow):
1. Print tags: `dimos apriltag --ids 0-5 --size-mm 100 --family tag36h11 -o markers.pdf`. 100mm
   matches `unitree_go2_markers`' default. **Print-scale check every sheet:** 100%/Actual Size
   only, caliper-measure the baked-in ruler — a 5% print-scale error is a systematic 5% range
   error on every fix.
2. Build the map with the robot, no hand measuring: `dimos run unitree-go2-markers`, let it see
   each tag from a couple of angles, read `marker_<id>` transforms off the TF tree (rerun viewer,
   on by default) into `office_markers.yaml`. Orientation matters more than position — a 2°
   orientation error displaces the computed camera pose ~3.5cm per meter of range.
3. Compose + run: `pytest dimos/robot/test_all_blueprints_generation.py` (regenerates the
   registry, fails "please commit" by design — expected), then
   `dimos run unitree-go2-visual-relocalization`.
4. **`--daemon` warning:** crashes on this Mac (`coordinator.start_rpc_service()` panics right
   after the fork — Zenoh's I/O driver doesn't survive it). **Always run foreground** in its own
   terminal/pane; `--daemon` leaves orphaned workers `dimos status`/`stop` can't see.

**Metrics logger + report** (attach to any running stack, pure observer, never publishes):
```bash
cd dimos
uv run dimos --replay --replay-db=go2_bigoffice run unitree-go2-visual-relocalization  # or real hw
# second terminal, once `dimos status` shows it running:
uv run python ../trial/scripts/metrics_logger.py \
    --out ../trial/scripts/out/run1.jsonl \
    --dimos-log "<Log path>/main.jsonl" --duration 480
# after:
uv run python ../trial/scripts/report.py \
    --log ../trial/scripts/out/run1.jsonl --out-dir ../trial/scripts/out/report1
# accuracy vs silver truth (lidar reference):
uv run python ../trial/scripts/report.py \
    --log ../trial/scripts/out/camera_run.jsonl \
    --reference-log ../trial/scripts/out/lidar_run.jsonl --out-dir ../trial/scripts/out/accuracy_report
```

**`bench.py`** — wraps the logger with `start`/`stop`/`report`, scores each run, appends to
`results/benchmarks.csv`, regenerates `results/RESULTS.md`:
```bash
cd dimos
uv run python ../trial/scripts/bench.py start --mode marker --route drift-recovery --notes "loop A"
uv run python ../trial/scripts/bench.py start --mode marker --route kidnapped-robot --notes "moved 3m"
uv run python ../trial/scripts/bench.py start --mode lidar  --route head-to-head --notes "pass 1: reloc"
uv run python ../trial/scripts/bench.py start --mode marker --route head-to-head --notes "pass 2: marker"
uv run python ../trial/scripts/bench.py start --mode fused  --route head-to-head --notes "pass 3: fused"
uv run python ../trial/scripts/bench.py report   # regenerate RESULTS.md + table
# add the start/end referee to any of the above:
uv run python ../trial/scripts/bench.py start --mode marker --route drift-recovery \
    --notes "loop A" --holdout-tag 42
```

**`holdout_overlay.py`** — one-window run controller: live camera view, frozen start/end tag
outline, START/STOP buttons, wraps `bench.py start`/`stop`. Auto-adopts the most prominent
unmapped tag in view as the start/end reference (`--holdout-tag` pins it instead). Box recolors by
distance-to-start (green <2cm / yellow 2-5cm / red >5cm); STOP pulses green once back within 2cm
for ~1s.
```bash
cd dimos
uv run python ../trial/scripts/holdout_overlay.py --mode marker --route drift-recovery --notes "loop A"
```
Run recipe: park facing any unmapped tag → START (outline freezes) → drive the loop → return until
green/pulsing → STOP (run scored, row appended). Imperfect runs still get recorded — never abort a
bad one, STOP it.

## 9. History — the arc so far

- **Jul 6** — Stash call (Dimensional CEO): FDE trial scoped around monocular localization.
  Weighed stickers/fiducials vs. Pudu-style ceiling vSLAM; picked fiducial markers (more
  achievable in a week, cleanly verifiable, directly additive to dimos's lidar-only stack).
- **Spec + simulated validation** — code spec written, `MarkerLocalizationModule` prototyped on
  branch `feat/marker-localization`, synthetic demo harness built (real `cv2.aruco` detector on
  rendered pixels, real drift/correction math). Dimos branch tests green, mypy strict clean.
- **PR #2808 progress** — module renamed `VisualRelocalizationModule` (capability-named). Grew to
  13 commits post-review: benchmark devtool (`dimos/mapping/benchmark/`), CLI (`dimos benchmark
  run|report`), metrics realigned to the honest checkpoint-error set (§6), Greptile round 3 5/5
  confidence, all 4 prior P1 findings fixed and verified in source.
- **Benchmark kit built** — start/end-tag referee, `holdout_overlay.py` run controller,
  `survey_dump.py`, in-repo benchmark port — verified pre-drive (§7: 6/6 components, 52/52 tests,
  mypy clean).
- **Trial started Jul 15** (SF office, 454 Natoma St) — robot "greenwald" connected (IP lesson:
  DHCP moves it, re-sweep 9991 if the physical tag's IP is stale). Presentation page built and
  live, posted to the team.
- **lesh's direction (review feedback)** — relocalization should be pluggable priors feeding ICP,
  not a standalone publisher; marker overrides RANSAC when visible; marker poses via stream now,
  K/V in map globally later; relocalization framed as "stolen robot" recovery; needs a CUDA
  machine for real village-scale runs; wants this run through Dimensional's own Linear-equivalent
  process.
- **Corrections made** — PR #2137 is RANSAC-tuning autoresearch, *not* the marker-ground-truth
  benchmark (that's `loop_closure/eval.py`, TOTAL_SPREAD) — see §6's "don't conflate" box. Ran
  village3: 4.955m spread, raw-vs-PGO 0.540m/0.577m RMS at n=4 (§7).
- **Plan v3 written** (§4) — the 4-phase post-review direction, package-based fusion end state.
- **Jul 16 (later, same day) — Plan v5** (Aaryan, latest burst): v4 draft (§2's "priors system"
  wording) superseded within the hour; plan rewritten around a universal confidence reading
  (Phase 1) → fiducial prior (Phase 2) → sections-of-PGO-maps testing with markers (Phase 3); the
  real-life benchmark — start/end-tag referee, live routes, kidnap runs, pass bars, field battery
  — cut (§4, §6). Phase 1 build in progress on branch `feat/reloc-priors` (window 1).
- **Jul 17 — Phase 1 verified · one branch · PR mishap · move to CUDA.** Phase 1
  (priors/universal-confidence system) built + adversarially verified (parity vs pre-refactor
  proven bit-identical; no-bypass tested); consolidated per Aaryan onto ONE branch
  `feat/fiducial-relocalization` (renamed from `feat/marker-localization-core`, all 16 commits,
  pushed to the fork). The rename CLOSED PR #2808 — cross-repo PRs don't follow fork-branch
  renames (mistake, lesson recorded; restore options in §3). Aaryan froze dimos-remote
  mutations until the work is verified (§2 CUDA queue = the gate) and moved to drive everything
  from the CUDA machine. Dimensional's Linear searched: DIM-940/DIM-920/DIM-944/END-76 are the
  carry-forward set (§3); ticket recommendation written, Aaryan posts. Trial repo confirmed
  only-main.
- **Open items:** see §2 Next actions.

## Team feedback (direction ledger)

A dated record of direction, scope calls, and process feedback the Dimensional team has given
Aaryan on this trial. Paraphrased and neutral — no verbatim quotes, first names only. This is the
input side of the arc above — what the team said, and what changed in response.

**Jul 8 — Stash (via email):**
- Skip elevators for the trial — none conveniently accessible at the office.
- Benchmark relocalization other ways instead; outdoor testing is fine.
- 1-2 weeks is enough time for the trial.
- Office hosting confirmed.

Acted on: the field battery (§6, "7-card field battery") was built with no elevator-dependent
card and an explicit outdoor-sightline test (card #3).

**Jul 15 (day 1) — Ari:**
- The day-one deliverable should be a scope doc plus a code skeleton, not a finished feature.

Acted on: a spec doc and a prototyped module skeleton (`feat/marker-localization`) were the
day-one output — see §9 History.

**Jul 15 — Mustafa B.:**
- Helped scope the trial project.
- Pointed to Lesh as the owner to go to for navigation/relocalization detail.

Acted on: the deeper technical direction below (Jul 16) came from that referral.

**Jul 16 — Lesh (main direction, Discord):**
- `RelocalizationModule`'s intended design is method-agnostic: pluggable relocalization
  heuristics/priors of varying confidence, running in parallel and individually toggleable.
- A fiducial marker prior is high-confidence global pose — the first prior he'd implement, calling
  it "clean." It hands its pose to the relocalization ICP alignment step and largely overrides the
  current RANSAC method when a marker is visible.
- Compute economics: don't run RANSAC when markers are visible; don't run marker recognition when
  no markers exist in the space.
- A weaker future prior — semantic-similarity / vector search over the space (per his memory-plot
  doc) — is an open question whether it nudges RANSAC or runs in parallel with it.
- Vocabulary correction: "relocalization" refers specifically to the stolen-robot problem (finding
  yourself on the map from an unknown pose). Odometry correction via pluggable methods is a
  separate, lower-priority track for now — he framed that track as live PGO-style heuristics
  (pluggable constraints on the pose trail, beyond today's lidar PGO at map-build time), said it
  lines up with the trial page's framing, and explicitly deprioritized it for now.
- Marker-pose storage: a stream for now; a K/V store under `dimos map global` later.
- The benchmark he'd point to already exists offline: marker-ground-truth over recordings, via
  `dimos map global hk_village4 --markers` (odometry test) vs. `--pgo --markers` (compare by
  marker agreement). `go2_hongkong_office` is the large tuning/eval map; villages 1-5 all observe
  markers. PR #2137 was an autoresearch task tuning relocalization from marker ground truth.
- Homework: go through the relocalization doc end-to-end and try it in person; the existing
  recordings support most of the development without needing the live robot.
- Process: the plan should go on Linear (Dimensional's own tracker) for team review; decisions get
  written down; map-related work should use the CUDA machine.
- Overall: the direction was endorsed; the team has existing work (the RANSAC tuning harness, the
  offline marker eval) that should speed this up.

Acted on: plan of record rewritten as v3 around pluggable priors and a fusion end state (§4); the
CUDA-machine task queue (§2) is built from his homework list; a Linear ticket was drafted (§5)
ready to post to Dimensional's own tracker; the "relocalization" vs. "odometry correction"
vocabulary distinction was corrected across the docs; PR #2137 vs. `loop_closure/eval.py` was
disambiguated (§6, "don't conflate these two").

*Updated whenever the team gives direction; feedback lands here same-day.*

## 10. Learning log — Aaryan's working vocabulary (ramped 2026-07-16)

Concepts taught to fluency, one line each, so any window knows what can be assumed in
conversation with him. ★ = deep-dived, can defend on a call, not just recite.

- Frames: `world` = odom origin (boot-relative, drifts, restarts) · `map` = premap frame
  (persistent) · `base_link` = body. Corrections land ONLY on the `world→map` edge — `base_link`
  never teleports. dimos `world` ≈ ROS `odom` (naming inverted vs. convention).
- Odometry/drift: self-counted motion; errors only accumulate; early heading error compounds;
  super-linear, unbounded.
- Relocalization = the stolen-robot problem (team vocabulary): global "where am I" from nothing.
  Distinct from continuous odom/drift correction — lesh's separate, deprioritized track (live
  PGO-style heuristic constraints on the pose trail).
- Their lidar pipeline: FPFH (neighborhood-shape fingerprints → candidate matches) → RANSAC
  (random minimal samples → inlier voting; truth is consistent, garbage isn't; fooled by
  self-similar spaces) → multi-scale ICP (a polisher, needs a seed) → fitness gate → world→map.
- Map build: loop closure = measured drift; PGO = springs relaxing over the pose trail
  (offline-only on stock Go2); voxel map → costmap.
- ★ PnP/IPPE + planar pose ambiguity: 4 uniquely-labeled corners → full pose from one frame;
  near-frontal, the mirror tilt slides every corner along its own viewing ray (cos +θ = cos −θ;
  only the perspective denominator differs — sub-pixel at 2 m). Not a labeling confusion;
  specific to flat targets. Fix: candidate-ratio gate ≥2.0 + park 20–30° oblique (measured:
  39 mm frontal vs 3–7 mm oblique).
- Benchmark honesty: ground truth must be external to the system under test (circularity trap);
  "return error vs fiducial GT," never claimed as ATE; TOTAL_SPREAD = marker self-agreement;
  recovery time; bounded-vs-unbounded; measure the noise floor before claiming differences.
- Fusion: sources emit candidate + confidence + health; marker = identity (high), RANSAC =
  consensus (medium), semantic = resemblance (weak — search-space hint only, never publishes);
  Mahalanobis-gate implausible jumps; never average a disagreement; "age of relocalization."
- ★ Stream vs K/V (lesh's storage line): stream = dimos-native broadcast, zero new infra, his
  "for now"; K/V = persistent `marker_id → pose` lookup under `dimos map global`, his "later."
  Same schema either way — swap the plumbing, not the math. Repo reality: §7 first finding.
- Semantic search: embed views into vectors, nearest-neighbor place recognition; coarse and
  aliasing-prone (resemblance, not identity) → weak prior that narrows RANSAC's search.
- Search vs tracking: relocalization = find-me from nothing (rare, global, jumps allowed) vs. odom
  correction = keep-me-found (constant, local, smooth nudges); today dimos corrects only the
  `world→map` lens, never the odometry trail, and only when a premap exists.

## 11. Between machines — read-only clone variant

The canonical setup (this repo as root, `dimos` cloned inside it) lives in §0 Cold start. Use this variant instead when handing a
**second machine you don't want full SSH access** a read-only copy of this repo (not `dimos`) —
scope: this repo only, 30-day expiry.

1. github.com → Settings → Developer settings → Fine-grained personal access tokens → Generate
   new token.
2. Resource owner: `AaryanAgrawal`. Repository access: **Only select repositories** →
   `dimensional-trial`. Expiration: **30 days**. Permissions → Repository permissions →
   **Contents: Read-only** (leave everything else at No access).
3. Clone with the token as the password over HTTPS:
   ```bash
   git clone https://<PAT>@github.com/AaryanAgrawal/dimensional-trial.git
   ```
