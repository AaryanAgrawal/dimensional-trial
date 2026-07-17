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
   git checkout feat/marker-localization-core
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
| The module + all code changes | `dimos` fork, branch `feat/marker-localization-core` (cloned inside this repo root, §0) |
| The PR | **#2808** — https://github.com/dimensionalOS/dimos/pull/2808 |
| The public presentation page | https://aaryanagrawal.me/dimensional |
| Trial page source | github.com/AaryanAgrawal/portfolio → `src/app/dimensional/` (deploys to aaryanagrawal.me/dimensional via `vercel --prod` from that repo's checkout) |
| This repo | context/plan/benchmark-protocol/history only — `dimos` sits inside this folder on disk but is its own git repo, gitignored, never tracked as content here |
| Benchmark instruments (logger, bench runner, referee, overlay, survey dumper) | `trial/scripts/` in this repo |
| Synthetic proof harness (real detector, rendered pixels, no hardware) | `demo/` in this repo — `cd demo && ./run.sh` |
| Physical marker kit (printable tags, surveyed map) | `print/*.pdf`, `office_markers.yaml`, this repo |
| Real benchmark run output (generated, not tracked) | `trial/results/` and `trial/scripts/out/` — created on first run by the scripts themselves |
| Everything else from the trial (spec docs, research notes, day-by-day roadmap, PR drafts, page copy) | local disk only, untracked — folded into this doc's sections below where still load-bearing |

## 2. Next actions

- [ ] todo — **test existing relocalization first** (Aaryan, Jul 16, supersedes ordering): exercise
      the existing lidar `RelocalizationModule` per the replay walkthrough in
      `docs/capabilities/navigation/relocalization.md` (record → `map global --export` →
      relocalize in replay → live) — this is the CUDA queue below (task 1), as already written.
      Re-plumb (Phase 2) and the fusion end state (§4) stay pending until after this.
- [ ] todo — post the Linear ticket (§5) to Dimensional's own tracker
- [ ] todo — lesh call: align on the fusion re-plumb (§4 end state) before building it — after the
      above
- [ ] todo — CUDA runs: villages 1-5 + go2_hongkong_office at full scale (see "Tasks — CUDA
      machine" immediately below — claim from there, not from this line)
- [ ] todo — re-plumb decision: Phase 2 (pluggable-prior refactor) timing vs. shipping Phase 1
      (live-axis benchmark extension) first — after the above
- [ ] todo — page comments feature — on hold, not blocking, revisit after the above

### Tasks — CUDA machine (window 2)

- [ ] 0. Sanity: confirm the map pipeline runs on CUDA — quick `dimos map global hk_village3 --markers --no-gui`; should be much faster than CPU (~3 min on the Mac). Fix device selection if not.
- [ ] 1. Walk docs/capabilities/navigation/relocalization.md end-to-end on a village recording: build the loop-closed global map, `--export` a premap, then run relocalization against it in replay — watch RelocalizationModule find itself on the map.
- [ ] 2. Odometry test: `dimos map global hk_village4 --markers --no-gui` (map + marker poses from raw odometry).
- [ ] 3. Comparison: `dimos map global hk_village4 --pgo --markers --no-gui` — compare marker agreement vs task 2 (avg distance between repeated marker sightings).
- [ ] 4. Full offline baseline table: repeat 2+3 across hk_village1..5 + `uv run python -m dimos.mapping.loop_closure.eval <village>` per village → per-village TOTAL_SPREAD + raw-vs-PGO marker RMS. (Reference: Mac CPU run of village3 = SPREAD 4.955m, raw-vs-PGO RMS 0.540/0.577, n=4.)
- [ ] 5. The big eval map: `dimos map global go2_hongkong_office --pgo --markers --no-gui` + its numbers.
- [ ] 6. Write all numbers/tables into ## Findings, note anything that behaved differently than documented, push.

Rules reminder inside the section (one line): claim a task by marking `[~] doing — window 2` + push (the push is the claim); finish with `[x] + one-line result`; do not touch the robot or push to the dimos fork/PR from window 2.

## 3. Current state (2026-07-16)

PR **#2808** on `dimensionalOS/dimos` is live at 13 commits — plan-of-record v3, post-review:
extend the existing offline marker benchmark with a live axis, treat the marker method as a
pluggable relocalization prior, and add a method manager + graceful degradation path. Module
renamed to `VisualRelocalizationModule` (capability-named, matches the trial page H1). Trial
project #1 (active) is the three-way relocalization benchmark: odom-only baseline vs. their
`RelocalizationModule` (lidar/premap) vs. this fiducial module — see §6 Benchmark below and
`trial/results/RESULTS.md`. Direction as of Jul 16 (Aaryan): test the existing lidar
`RelocalizationModule` first (§2 top action) before further re-plumb/fusion work.

## 4. Plan of record (v3-final)

Post-review direction for PR #2808, four phases:

- **Phase 1 — Benchmark: live-axis extension.** Reproduce the offline eval (done — village3, see
  §7 Findings), then extend `dimos/mapping/benchmark/` with a live axis: return error vs. a
  held-out marker, kidnap→recovery time, bounded-vs-unbounded drift, per method.
- **Phase 2 — Marker prior: re-plumb.** Turn `VisualRelocalizationModule` from a standalone
  `world->map` publisher into a pluggable, toggleable high-confidence prior feeding the
  relocalization ICP step — overrides RANSAC when a marker is visible.
- **Phase 3 — Benchmark all.** odom-only vs. RANSAC→ICP vs. marker→ICP, offline (villages +
  go2_hongkong_office) and live.
- **Phase 4 — Method manager + runtime degradation.** Parallel/toggleable/confidence-weighted
  method manager, compute-aware, tracking "age of relocalization." Knobs tuned by autoresearch
  against the benchmark (committed to the plan, Aaryan Jul 16 — see the autoresearch block below).

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
- **A read-only harness** — the Phase 1 benchmark with its start/end-tag ground truth (replay
  recordings + kidnap variant), fixed time budget, deterministic seeds. This is the "better data —
  fiducial markers as ground truth" the #2137 next-steps section asked for.
- **A results ledger** (`results.tsv` style) with a strict metric hierarchy — return error vs
  fiducial GT, then kidnap recovery time, then compute cost — keep/discard per experiment, every
  row reproducible.
The agent grinds the knob space against the referee; humans review the ledger, not the guesses.

## 5. Linear ticket (draft, ready to post)

> **Title:** Relocalization — marker prior + live benchmark extension (trial project)
>
> **Context:** RelocalizationModule direction = method-agnostic pluggable priors feeding ICP (per
> review); today: FPFH+RANSAC+ICP global reloc (tuned via the #2137 autoresearch harness) + offline
> marker-ground-truth eval (`loop_closure/eval.py` marker-spread over hk_village recordings); this
> trial adds the highest-confidence prior (fiducial markers) + extends the eval to live on-robot
> behavior.
>
> **Phase 1 Benchmark live axis:** reproduce offline eval (DONE village3: TOTAL_SPREAD 4.955m;
> raw-vs-PGO RMS 0.540/0.577m, n=4 — rerun bigger sets on CUDA); extend with return error vs
> held-out marker, kidnap→recovery time, bounded-vs-unbounded, per method; instrument on branch
> (`dimos/mapping/benchmark`, `dimos benchmark run --mode odom|lidar|visual`).
>
> **Phase 2 Marker prior:** re-plumb PR #2808 module from standalone world→map publisher to
> pluggable high-confidence prior feeding relocalization ICP; toggleable; overrides RANSAC when
> visible; marker poses via stream (K/V in map global later).
>
> **Phase 3 Benchmark all:** odom · RANSAC→ICP · marker→ICP, offline (villages, go2_hongkong_office)
> + live.
>
> **Phase 4 Method manager + runtime degradation:** parallel/toggleable/confidence-weighted,
> compute-aware, "age of relocalization."
>
> **Deliverables:** live benchmark extension · marker prior integrated · three-method table ·
> method-manager design (+impl if time).
>
> **Open questions:** semantic prior nudge-vs-parallel · stream vs K/V timing · amend #2808 vs
> follow-up · where live results live.
>
> **Needs:** CUDA machine; review @lesh.

## 6. Benchmark

The dimos relocalization benchmark — a fair, repeatable, physically-verifiable comparison of
dimos's localization correctors (odom-only, lidar-ICP-vs-premap, AprilTag marker correction) on
one Go2, one office, one instrument. Instrumented by `trial/scripts/bench.py`. Ratified before any
run counts, so nobody moves a goalpost after seeing a number.

### Routes & modes

- **Loop** (~30m, taped start/end) — drift accumulation + recovery-on-reacquisition, the headline
  number.
- **Corridor** (longest feature-poor stretch, ≥15-20m) — stresses ICP's along-corridor aperture
  degeneracy, the one axis neither odometry nor lidar constrains well.
- **Kidnap** (carry the robot ~3m mid-run, or cold-start displaced) — bootstrap-free
  relocalization: closed-form single-frame PnP vs. iterative ICP with no prior.
- Modes: **odom** (dead-reckoning floor) · **lidar** (`RelocalizationModule` + same-day premap) ·
  **marker** (`VisualRelocalizationModule` + self-surveyed map) · **fused** (deferred — no
  arbitration design exists yet, see §4 end-state; scoring it now would score undefined behavior).
- **Capture:** odom and marker are scored off the *same* drive — `VisualRelocalizationModule`
  always runs and corrects, but retargets its publish off the live `map` frame
  (`-o visualrelocalizationmodule.map_frame=map_marker`) so one physical drive doubles as a clean
  odom-only baseline and a marker-corrected trace. Lidar joins this combined run once
  `RelocalizationModule` shares the drive with the shadowed marker corrector (Phase 3).

### Metrics — the honest, research-grounded set

Full ATE/RPE (TUM/KITTI-style) need continuous ground truth at every frame — this setup only has
GT at discrete tag sightings (start/end, or wherever a held-out marker appears), so a "full ATE"
label would silently overclaim continuous coverage that doesn't exist. The honest move (same math
as one term inside KITTI's drift-%, or as loop-closure error): report a checkpoint relative-pose
measurement, not a trajectory curve.

Let `T_start_gt`, `T_end_gt` be the known tag poses (same marker revisited ⇒ closed loop), and
`T_start_est`, `T_end_est` the pipeline's estimated poses at those instants:

```
ΔT_gt  = T_start_gt⁻¹ · T_end_gt        (known true relative transform)
ΔT_est = T_start_est⁻¹ · T_end_est      (pipeline's estimated relative transform)
E      = ΔT_gt⁻¹ · ΔT_est

e_pos  = ‖trans(E)‖                     (meters)
e_yaw  = angle(rot(E))                  (degrees, planar/ground-robot case)
```

- **① Return / checkpoint error** (above) — PRIMARY. TUM's RPE formula with N=1, over the one
  interval GT actually covers.
- **② Drift ratio** = `e_pos / distance_traveled × 100` — KITTI-style, one sample instead of
  hundreds.
- **③ Max checkpoint error** = `max_k ‖p_est,k − p_gt,k‖` over every instant a tag is visible.
- **④ RMS over GT-visible samples** (only if N>2) — same shape as ATE, honestly scoped to
  checkpoints not frames.
- **⑤ Recovery time / success rate** (kidnap only) — `success=1` if `e_pos < tolerance` (e.g. 0.5m)
  is reached and held before the tag sighting; `recovery_time` = frames/distance from perturbation
  to that point.
- **⑥ Bounded vs. unbounded classification** — run ① across varying path lengths; flat/asymptotic
  ⇒ bounded (anchored to the marker), growing ⇒ unbounded (pure dead-reckoning between sightings).

**Neutral names for any page/report:** Return error · Drift ratio · Max checkpoint error ·
Recovery time · Bounded vs. unbounded error growth. Avoid "ATE"/"RMSE" as headline labels unless
qualified ("checkpoint RMSE, N=2") — those carry a continuous-GT connotation this setup can't back.

`bench.py` also computes, from the log directly: **loop-closure error** (odom vs corrected,
start==end taped mark), **time-to-recover** (kidnap, wall-clock to first `correction_new`),
**corrections accepted/rejected/held** (from log records), **corrected-pose coverage %**, and an
**ATE-proxy** where a `--reference-log` exists (RMSE vs. that reference run — relative only, no
external ground truth).

### The start/end-tag referee (`--holdout-tag`)

An automated, map-independent end-pose check on top of any run: print one extra tag (any free ID,
e.g. 42), leave its ID out of every marker map under test, tape it up where the camera sees it at
drill-start and drill-end. `metrics_logger.py` runs a second, independent solvePnP for that tag
only; `bench.py stop`/`report` take the median of the first/last 10 sightings as the physical
truth and diff it against whatever the mode under test claimed over the same window — an automatic
loop-closure-error-vs-real-reference number instead of an assumed taped start==end. **Shared-
pipeline caveat:** the referee's PnP runs the same detector/solvePnP code, on the same camera, as
the mode it's checking — not a fully independent instrument, hence the calibration below.

**Noise floor + bias, once per session, before the first scored run:**
```
cd dimos
uv run python ../trial/scripts/bench.py calibrate-referee --holdout-tag 42
```
STEP 1: a static hold (default 60s, don't move) computes per-axis std + a 3-sigma radius for
position, std/3-sigma spread for rotation — should be well under 2cm radius. STEP 2: slide the
robot a measured distance (≥1.0m recommended) along a straightedge, enter the tape-measured value
— reports scale error %. Writes `trial/results/referee-budget.json`; every `bench.py report`
afterward appends a `start/end tag referee: ±Xmm (3-sigma), scale bias Y% (calibrated <date>)`
footer to `RESULTS.md`. Re-run whenever the rig changes (new tag print, moved camera).

*Heavier option, week-2+ escalation only if the marker-vs-lidar gap ever needs sub-mm resolution:*
a mechanical registration jig (carpenter's-square fence) + a fixed overhead ChArUco board (never
AprilTag — must be a different marker family so it's never confusable with the tags under test) +
a laser distance meter, noise-floor-tested ISO-9283-style (≥30 cycles, `RP = barycenter deviation +
3σ`). Not the Project 1 method — not built.

### Fairness rules

Same route/pace across every mode in a comparison. Lidar runs its best case (same-day premap of
the exact route, never stale). Marker map only from the standard self-survey flow (§8 Runbook) — no
hand-tuned tag positions. Every run lands in `results/RESULTS.md`, including failed/rejected runs —
no discarding a bad take. Fixed gates for the whole benchmark (ambiguity ratio 2.0, reprojection
3px) — no per-run retuning to flatter a number. n≥3 runs per (route, mode) cell before any number
is quoted.

### Pass bars

Marker ≥3x loop-closure improvement over odom-only (a floor — the synthetic demo suggests 5-7x).
Kidnap: recovery <10s with tag visible; lidar showing no recovery in this window is expected, not a
failure (no closed-form kidnap solution). Corridor: marker-corrected run holds bounded lateral
drift; odom/lidar-only shows unbounded along-corridor drift by the far end.

### The 7-card field battery (no elevator required)

Stash's steer (call, 2026-07-08): test without elevators, outdoor is fine. Every number below is a
first real-hardware-run estimate, hedged down from the synthetic demo's 5-7x. Demo-day pick:
**#1 + #2** — both run in minutes, zero narration, best theater.

| # | Card | What it proves | Pass bar |
|---|---|---|---|
| 1 | Drift-recovery loop | Tag-corrected beats odom-only drift, real hardware | Route drifts >0.3m odom-only; corrected ≥3x better |
| 2 | Kidnapped robot / cold start | Single-shot PnP needs no bootstrap, unlike ICP | Marker publishes within one detection cycle (~<1s) vs. lidar's multi-cycle (2s+ poll) |
| 3 | Outdoor sightline | Tags work where lidar-premap is structurally weak | ≥80% detection within 3m, worst lighting bucket |
| 4 | Long corridor / feature-poor | ICP's along-corridor aperture problem; a tag supplies the missing constraint | Lateral dev ≤0.15m, along-corridor error ≤0.3m at far end (corrected) |
| 5 | Glass/reflective lobby *(conditional)* | Specular surfaces wreck lidar ICP; tag ID doesn't care what's behind it | Measurable lidar fitness drop/rejection near glass; marker detection indistinguishable from control |
| 6 | Head-to-head vs. RelocalizationModule | The single go/no-go number: lidar vs marker vs fused | Fused ATE ≤ better of the two solo runs |
| 7 | Dynamic clutter *(bonus)* | ICP fitness degrades on unmapped clutter; tags don't care | Marker ATE degrades <20% clear→cluttered |

Grounding for the numbers above: measured envelope for real tags — 150mm tags detect ≥95% to
4m/55°, trusted single-frame pose tightest inside ~1m; the trial's 100mm tags scale to roughly
~2.5-3m/~45° detection, same trusted-pose window (conservative 2/3 scaling).

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
