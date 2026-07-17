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
| The module + all code changes | `dimos` (cloned inside this repo root, §0) — ONE working branch **`feat/fiducial-relocalization`** (local + fork, identical), all 16 commits: the reviewed marker-localization module + the verified Phase 1 priors system. The fork is the PR channel (fork-and-pull — standard practice, Aaryan confirmed keep, Jul 17; **never delete the fork**). The fork also still has `feat/marker-localization-core` (head of closed #2808) — delete only on Aaryan's word. |
| The PR | **#3016** — https://github.com/dimensionalOS/dimos/pull/3016 (supersedes #2808, closed Jul 17 with a pointer — same history, correctly-named branch) |
| The public presentation page | https://aaryanagrawal.me/dimensional |
| Trial page source | **`site/` in THIS repo (canonical copy — window 2 owns the website lane, Aaryan Jul 17)**: `page.tsx` (route), `data-dimensional.ts` (content data, lives at `src/data/dimensional.ts` in the portfolio repo), `assets/` (`public/dimensional/` there). Deploy home stays github.com/AaryanAgrawal/portfolio — to ship: copy the three pieces into the portfolio checkout, `npx tsc --noEmit && next build`, `vercel --prod` (scope servicerobotco, project aaryan-portfolio). Edit here first, sync on deploy — keep both in step. |
| This repo | context/plan/benchmark-protocol/history only — `dimos` sits inside this folder on disk but is its own git repo, gitignored, never tracked as content here |
| Benchmark instruments (logger, bench runner, referee, overlay, survey dumper) | `trial/scripts/` in this repo |
| Synthetic proof harness (real detector, rendered pixels, no hardware) | `demo/` in this repo — `cd demo && ./run.sh` |
| Physical marker kit (printable tags, surveyed map) | `print/*.pdf`, `office_markers.yaml`, this repo |
| Real benchmark run output | `trial/results/` and `trial/scripts/out/` — generated, untracked; EXCEPT `trial/results/figures/*.png` (comparison graphs — tracked, shared between machines) |
| Everything else from the trial (spec docs, research notes, day-by-day roadmap, PR drafts, page copy) | local disk only, untracked — folded into this doc's sections below where still load-bearing |

## 2. Next actions

**NAMING (Aaryan, Jul 17 morning — use these terms everywhere from now on):**
- **"Relocalization benchmark"** = the MARKER instrument: physical tags as external truth —
  the revisit test (observe → walk → observe again), marker scatter, marker-truth scoring,
  the duplicate-id/cluster validity check. (`trial/harness/markers.py`,
  `allaround_profile.py`, the six-village sweep.)
- **"Relocalization confidence"** = the SEPARATE track: the universal confidence reading
  (pluggable priors → one shared judge → one published score + source) and its quality
  analysis — risk–coverage, AUROC, calibration, data-grounded accept gates.
  (`priors.py`/`relocalize.py` on the branch; `trial/harness/{prep,run_bench,analyze,confidence}.py`.)
- **Runtime split (Aaryan, Jul 17): relocalization confidence RUNS REAL-TIME** — it is what
  decides between priors on-robot (today: one judge, source-blind, winner-take-all — live on the
  branch) and later fuses them (Phase 4 arbiter: calibrated composite confidence = fitness +
  submap size + source tier + age + marker-innovation; fuse only agreeing candidates, never
  average a disagreement). **The relocalization benchmark runs OFFLINE** as the referee that
  calibrates those knobs; its runtime echo = the live marker-innovation spot-check.
- Morning Linear tidy-up (remote, Aaryan or on your word): retitle DIM-1254 to match
  ("relocalization benchmark (marker-truth, offline)"), keep DIM-1252 as the confidence track.

**GOAL OF RECORD (Aaryan, Jul 17, via chat — window 2 executes end-to-end, best-judgment
decisions):** (1) an industry-backed way to **benchmark relocalization confidence** — not just
success rate, but whether the published confidence *predicts* true error (risk–coverage /
calibration against PGO silver truth + marker agreement; grounds the 0.45-vs-0.6 fitness-gate
question in data instead of folklore); (2) **priors wrapped in ICP** (the Phase 1 system — built,
re-verified 6/6 on this box Jul 17); (3) **the fiducial prior on top, gated by confidence + age**
(Phase 2 + the Phase 4 monitor elements). Sequence: benchmark first, then wire, then fiducial.
Constraint kept: no pushes to the dimos fork/PR from window 2 until work is verified — local
dimos commits OK, harness code lives in THIS repo (`trial/harness/`).

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
      failure proven pre-existing at the base commit (LCM-timing flake). Pushed to the fork; live in
      **PR #3016**. Remaining for Phase 1 close: Phase 3 verification runs + team review.
- [ ] todo — **Phase 2: fiducial prior**: plug the visual module's `world→map` estimate (PR #3016) in as a
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
- [ ] todo — website update to v5 — **window 2's lane now** (Aaryan Jul 17); source is `site/` in
      this repo (§1), page still shows the old benchmark framing; deploy via the portfolio repo
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

**dimos on this machine (set up Jul 17):** the pre-existing clone at `~/dimos` (25G data, synced
venv, fork remote already configured, also used for non-trial work on this box) is **symlinked**
into the repo root (`dimensional-trial/dimos → /home/dimos/dimos`) — satisfies §0's layout without
moving 40G or breaking the machine's other references. Local branch `feat/fiducial-relocalization`
@ `a6be7e42e` tracking `fork/feat/marker-localization-core` (16 commits over the origin/main
merge-base). Phase-1 suite **re-verified on this machine: 6/6 green** (`uv run pytest
dimos/mapping/relocalization/test_relocalize.py`). Data present: `hk_village3.db` (325MB, real —
§7's "no villages" blocker is now partial), `china_office.db`, `go2_china_office.db`,
`go2_short.db`. Still missing for the CUDA queue: `hk_village{1,2,4,5}.db`,
`go2_hongkong_office.db` → `git lfs pull --include="data/hk_village*.db" --include="data/go2_hongkong_office.db"`.

*(Window 2: after the PR transition below, retrack — `git fetch fork && git branch --set-upstream-to=fork/feat/fiducial-relocalization feat/fiducial-relocalization`; the old fork branch name is now only closed #2808's head.)*

**Code state — one branch, one PR (final form, Jul 17):**
- **PR #3016 is THE PR** — open, 16 commits, from fork branch `feat/fiducial-relocalization`
  (Aaryan's chosen name), title "perception/fiducial: marker-map robot localization + pluggable
  relocalization priors". **#2808 is closed-superseded** with a pointer comment both ways — its
  review history remains readable there.
- **Fork-and-pull is the confirmed standing practice** (Aaryan, Jul 17: "if it is then keep doing
  this") — Aaryan has no write access to dimensionalOS/dimos (live-verified 403), so the fork is
  the only PR channel. NEVER delete the fork. NEVER rename a fork branch behind an open cross-repo
  PR (closes it — happened once, reverted).
- Optional, Aaryan's call: ask the team for write access → branch moves onto their repo, fork
  retires. Not required for anything current.
- The fork still carries `feat/marker-localization-core` (closed #2808's head) — harmless;
  delete only on Aaryan's word.

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
- **Phase 2 — Fiducial as prior.** The visual module's `world→map` estimate (PR #3016) proposes
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
> **Phase 2 Fiducial prior:** the visual module's `world→map` estimate (PR #3016) proposes into the same judge
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
> **Open questions:** semantic prior nudge-vs-parallel · stream vs K/V timing · review cadence on #3016 vs
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
### CUDA-machine window — 2026-07-16 (RTX 5070 Laptop, Ubuntu 24.04)

**Task 0 done — CUDA map pipeline confirmed.** `dimos map global go2_short --export --no-gui
--device CUDA:0`: 461 lidar frames → 154 keyframes (dedup 33.4% @ tol=0.3m), **2 loop closures**
(score 0.0984 f67→1; 0.0653 f92→0), PGO pass 1 35.6× realtime / pass 2 41.9× realtime, ~2 min
wall. Wrote `go2_short.pc2.lcm` + `.rrd`. Ran on `go2_short`, not a village — see next item.

**CORRECTED — an earlier revision of this section claimed the villages were missing and that
`eval.py` would silently print zeros. Both parts were wrong; retracted here rather than edited
away.** The false claim came from an `ls data/ | head` that truncated the listing (`hk_village3.db`
sorts *after* `go2_short.db`), and a source-read that missed `get_data`. Corrected by running it:

- **`hk_village3.db` was present all along** (340 MB). Absent: villages 1/2/4/5/6 +
  `go2_hongkong_office` — but see next point, absence appears to self-heal.
- **`get_data` (`utils/data.py:259`) auto-pulls AND decompresses from LFS on demand**, and
  `eval.py` calls it — so a missing recording is very likely fetched, not silently zeroed. The
  `must_exist` concern was reasoned from source without executing it. **Still unverified:** the
  absent-village path (an `eval.py hk_village4` run was cut off before it completed). Verify
  before trusting any village number, but do NOT treat this as a blocker.

**VERIFIED — task 4 partial, and the existing benchmark reproduces across machines.**
`uv run python -m dimos.mapping.loop_closure.eval hk_village3` on this box:
**`TOTAL_SPREAD=4.959`** vs the Mac CPU run's **4.955** (§7 above) — a 4 mm delta across two
machines and two OSes, so the metric is near-deterministic and the Mac number is trustworthy.
Loop closures found: score 0.0302 s198→t119; 0.0152 s216→t45. `WALL_TIME=17.83s`.
**`TOTAL_PGO_TIME=4.87s` vs the Mac's 2.22s — the CUDA box is 2.2× SLOWER at PGO.** Expected on
reflection (PGO is GTSAM/iSAM2 on CPU; the GPU does nothing for it, and `eval.py` takes no
`--device` flag at all) — but it undercuts the "villages need the CUDA machine" premise: **the
CUDA box only helps the voxel/map-rebuild half (`dimos map global --device CUDA:0`), not the eval
itself.** Worth knowing before scheduling the full 6-village sweep here.

**Undocumented Linux setup, once per boot** — without it every `dimos run` dies with
`CalledProcessError` before doing anything (dimos names the exact cmd in its own error):
```bash
sudo ip link set lo multicast on
sudo ip route add 224.0.0.0/4 dev lo
```

**`china_office.db` is not what any section assumes** (9.32 GiB, 2026-06-12, ~20 min) — and it may
carry the three-way comparison offline, no robot:
- **`lidar` stream = 0 items.** Data lives under `livox_lidar` (11,525 @ 9.8Hz, 3.50 GiB),
  `go2_lidar` (8,572 @ 7.3Hz), `pointlio_lidar` (11,944 @ 10Hz). `dimos map global china_office`
  defaults `--lidar lidar` → would silently build nothing. Pass `--lidar livox_lidar`. **Which
  lidar you pick is itself a benchmark axis.**
- **`april_tags_raw`: 3,959 detections @ 3.5Hz** (+ `april_tags`: 39). Real marker data, real
  office, alongside two lidars — the marker-vs-lidar comparison may already be latent here.
- `gt_pointlio_lidar` / `gt_pointlio_odometry` (35,699 @ 29.9Hz) — **treat the `gt_` prefix as
  unearned until verified.** Almost certainly PointLIO's own estimate, i.e. PointLIO judging
  PointLIO: correlated errors, exactly the trap the held-out-tag referee exists to avoid. The
  AprilTags are the only genuinely independent instrument in this file.
- 6× `pointlio_odometry__surf0p1_map0p2`-style variants = someone's parameter sweep.
  Also `tf` 58,964 @ 49.4Hz, `go2_odom` 21,957 @ 18.7Hz, `color_image` 16,740 @ 14.2Hz.
- `go2_short` (2026-05-06, 59.8s): lidar 461 @ 7.7Hz · color 855 @ 14.3Hz · odom 1,122 @ 18.7Hz ·
  plus `color_image_embedded` 108 (the CLIP/`visual_memory` layer).
  `go2_china_office` (2026-04-28, 138.8s): lidar 982 @ 7.1Hz · color 1,951 · odom 2,602.

**Branch docs are stale vs `main`.** `docs/capabilities/navigation/relocalization.md` differs by
**148 insertions / 70 deletions**. `main` adds the "Quick validation" section (`dimos mem summary`
/ `dimos map replay --duration 60`), a flags table (`--pgo-tol`, `--voxel`), a config reference
(`fitness_threshold` **0.45**, `MIN_LOCAL_POINTS` 50_000, `RELOC_INTERVAL`/`PUBLISH_INTERVAL` 2.0s,
last three not CLI-overridable), and a troubleshooting table. **Read `origin/main`'s copy.** Its own
line — *"You can replay a different `.db` from the same physical space against the same premap to
test generalization"* — is lesh's cross-run test, documented and unrun.

**Eval-landscape audit** (55-agent sweep, every claim adversarially verified against source):
- **Zero recorded benchmark runs exist anywhere.** No `benchmarks.csv`, no `benchmark_results/`,
  no `trial/scripts/out/`, no `referee-budget.json`. `RESULTS.md` says "No runs yet."
- **`calibrate-referee` was never ported** into `dimos/mapping/benchmark/` (zero hits for
  `calibrate|noise_floor|bias_check`) — so **every return-error number the repo tool emits today
  has unknown error bars**, and §6's "≥3× improvement" pass bar is unfalsifiable: a ratio between
  two uncharacterized numbers. The code exists at `bench.py:369-428` with a hardware-free selftest
  (`:993`); ~200 lines, mechanical port onto `tool_benchmark.py`'s existing `_median_pose`/
  `_quat_angle_deg`. Highest-value build item, and it should land **before** the first scored run.
- **Three metrics implemented but never once executed:** checkpoint error (no fixture yields >2 tag
  clusters, so `checkpoint_error_rms_m` has never run), recovery time (`FixtureCase`
  (`testdata.py:98-105`) has no `variant` field → the `:478` guard short-circuits, field is always
  `None` in tests), bounded/unbounded classifier (no direct test). All three render as `RESULTS.md`
  columns with nothing behind them.
- **"Held-out" is not enforced.** Without `--marker-map`, `map_ids` stays empty → *any* stable tag
  becomes a referee candidate (proven by the branch's own
  `test_tag_adopter_any_stable_tag_adopts_with_no_marker_map`). For **lidar** mode exclusion is
  vacuous entirely — a marker map can't describe a point-cloud premap. Yet `cli.py:645-647`
  footnotes the guarantee into `RESULTS.md` anyway.
- **`TOTAL_SPREAD` is gameable and un-normalized:** raw pairwise sum, and `_pairwise_sum` of a
  1-element list returns `0.0` — so tightening `--marker-quality-window`/`--marker-max-speed`
  drops detections and *lowers* (improves) the score. It also rewards a consistent-but-wrong map.
  `eval.py` exposes **zero** PGO knobs (`PGO()` built with no args; `PGOConfig` has 17 fields, none
  reachable), emits no machine-readable output/threshold, and exits 0 regardless of score.
- **The production `relocalize.py` has no accuracy eval on `main` at all** — shipped via #2160,
  tuned constants unverifiable in-tree; it still cites `program.md`, a file absent from `main`.
- **PR #2137 errata:** it is **OPEN**, on branch **`sloptimization/ransac`**. Its harness needs a
  ~3-line adapter to run against `main`'s `relocalize()` (main returns a 2-tuple; `run.py:107-113`
  expects a bare (4,4) → `ValueError` on ragged input, *outside* the try/except → unhandled
  traceback on the first completed frame). Known defects to fix during any port: it scores **55
  frames, not 60** (`run.py:155` excludes `{0,72,1005,1507,2942}`), and its **soft timeout silently
  shrinks the denominator** — unfinished frames are dropped and `succ = ok.mean()` averages only
  completed ones, so a budget overrun reads as success rather than failure.

### CUDA-window — Jul 17 night: recon digest + benchmark design (all claims execution-verified by a 6-agent sweep; full reports in session scratchpad, load-bearing facts here)

**Corrections to earlier beliefs (each verified by running code):**
- **PGO is NOT bit-deterministic**: same input, same box, 3 runs → keyframe count stable (220) but
  optimized poses differ up to **6.0 cm**, one loop-closure target flipped between runs, TOTAL_SPREAD
  4.955 (Mac) / 4.959 / 4.960 (here). No seed knob exists. **~6 cm = the silver-truth noise floor**;
  every "error vs PGO" number carries it as an error bar. (There is NO PGO accuracy eval anywhere
  in-repo — TOTAL_SPREAD is self-consistency only; PGO-as-truth remains unproven, measured tonight
  via marker scatter at real n.)
- **The published fitness is the STAGE-2 wall-only Tukey-ICP fitness** of the winning candidate
  (relocalize.py:237,248), not the final full-cloud ICP fitness. This exact number is what the
  confidence benchmark evaluates. Accept gate: fitness >= 0.45 (module.py:56,158; docs' 0.6 stale).
- **`eval.py` crashes on china_office** (verified: LookupError at .last()) — its `lidar` stream
  exists with 0 rows; no fallback to livox/pointlio streams.
- **BUG in our own branch (PR #3016 code): `visual_relocalization.py:84,90` never passes
  `distortion_model` to PnP** — Go2 camera is `equidistant` FISHEYE (front_camera_720.yaml); its 4
  coeffs get misread as radtan k1,k2,p1,p2. `marker_detect.py:91,115` and benchmark cli.py do pass
  it. Fix + test = Phase 2 work item (tonight).
- **#2137's data-prep code was never committed anywhere** (`dimos.mapping.prepare` is a dangling
  reference; blobs committed opaque). Empirically recovered: global_map = 0.05m two-pass PGO voxel
  map of go2_hongkong_office; 60 evenly-spaced test centers, body-frame accumulated submaps
  (20-57k pts), gt = SLERP-interpolated PGO pose. Its 2 defects confirmed at exact lines (5 frames
  hard-excluded incl. 3 "not disambiguable without a pose prior" — exactly the frames a fiducial
  prior must re-include; timeout silently shrinks the success denominator — results.tsv rows show
  it happening in real runs).
- **`color_image.pose` semantics differ per recording** (verified numerically): hk_village3 =
  world_T_optical (correct for DetectMarkers); go2_short = world_T_base_link (markers would be
  misplaced by the mount); go2_china_office = NO poses (0/1951 — DetectMarkers yields nothing
  without pose_fill re-posing); china_office = matches NOTHING tested (unknown provenance — derive
  camera pose from go2_odom/tf instead).
- **china_office lidar for PGO**: only `gt_pointlio_lidar` satisfies PGO's contract ('odom'-frame
  clouds + poses). livox/go2_lidar have no obs.pose; pointlio_lidar clouds are sensor-frame
  (50 keyframes/0 loops on a 300-frame probe — geometrically wrong). Its `april_tags_raw` = 3,959
  camera_T_tag PoseStamped rows (ids {1,3,5,92,93,94,96,97,98}, tag36h11 verified, ~0.1 m tags,
  per-row health: reproj_px/sharpness/speed) — written by an external recorder, no in-repo writer.
- **No real marker map exists in-repo** (office_markers.yaml is an explicit placeholder);
  map_T_tag must be derived from the PGO run itself (eval.py pattern) — same as a deployment
  survey would, so fine for evaluating the PRIOR (the judge still decides).
- **`dimos map global` writes no machine-readable trajectory** — silver truth requires re-running
  PGO offline (`lidar.transform(PGO()).last().data`; village3 ≈ 5 s). `--device CUDA:0` only
  accelerates VoxelGrid accumulation; relocalize.py is legacy-o3d CPU-only; PGO is CPU.

**Benchmark design (decided, building now in `trial/harness/`):**
- **Sections harness**: per query frame — trailing-window VoxelGrid accumulation (carve ON,
  live-mimic) until >=50k pts (the live gate), re-anchored to the query frame's body frame;
  relocalize against the offline-rebuilt PGO premap (carve OFF, CLI-mimic); truth =
  world_corrected_T_body(ts) from the same PGO graph. Success = <1 m AND <15° (#2137-comparable).
- **Full accounting, no exclusions**: every frame counted; crashed/unfinished = failure in the
  denominator (fixes both #2137 defects); per-frame seeds = frame_idx (o3d+np+random),
  OMP_NUM_THREADS=1 before o3d import, process-per-frame pool, sorted order.
- **Confidence quality is the headline metric** (trial/harness/confidence.py, 8/8 unit tests
  green): risk–coverage curve (selective prediction) → data-grounded accept threshold vs the 0.45
  folklore; AUROC (does fitness rank success); reliability/ECE (is 0.7 "70% sure"); operating
  points at 0.45 AND 0.6 (answers the code-vs-docs discrepancy with data).
- **Configs**: ransac-only (today's stack) · +last-pose (Phase 1 flag) · +fiducial (Phase 2, after
  the branch work) — same judge, same frames, same seeds; per-source attribution from
  winning_source.
- **Marker-agreement experiment** (PGO-accuracy evidence at real n): DetectMarkers per-sighting
  world poses, raw vs graph.correct()ed scatter per marker id — hk_village3 first;
  (china_office variant EXCLUDED per Aaryan Jul 17; production-lane version to come from the
  purpose-built mid360 walk recording). If PGO doesn't tighten scatter, vs-PGO numbers demote to indicative.
- **Truth labels on every number**: `replay` rung, truth = "PGO silver (±6 cm floor)" or "marker
  agreement (independent)". Nothing labeled plain "ground truth".

**First results (village3, 24 sections, replay, truth = PGO silver) — the money finding:**
success 79.2% (19/24); **all 5 failures are CONFIDENT busts** — fitness 0.81–0.93 on poses
1.4–3.9 m / 70–160° wrong; every one passes the 0.45 gate AND the docs' 0.6 (a success exists at
fitness 0.824 *below* a failure at 0.934 — the published confidence is nearly blind at the failure
boundary here). All 5 failures are small-submap frames (7.6–35k pts, below the live 50k gate) —
**MIN_LOCAL_POINTS, not fitness, is what protects the live robot today.** Failure mode = 70–160°
rotations (the yaw-flip/wrong-room class #2137 documented — the class a fiducial prior kills).
120-section runs + fiducial configs running.

**PGO-truth verification (leshy's method: observe marker → drive loop → observe again, locations
must match — stratified by sighting time gap, marker 10, 156 sightings, village3):**
- **RETRACTION of tonight's earlier aggregate claim "PGO makes marker scatter worse (0.387→0.478 m
  RMS)"** — true as computed but a composition artifact: short-gap pairs (no drift to correct)
  dominate the mixture. Kept visibly per anchor-on-truth.
- Stratified truth (all four bucket numbers adversarially re-derived, exact): 60s+ loop-return
  pairs raw 0.934 m → PGO 0.280 m (leshy's verification PASSES — caveat: 78% of those pairs are
  first-vs-last-visit; pairs touching the bad pass below stay ~1.49 m). 30–60 s pairs raw
  0.471 m → PGO 1.375 m. 0–10 s: 4–5 mm both.
- **Mechanism (my first reading REFUTED by the verifier; this is the ablation-supported one):**
  the 30–60 s damage is ONE misplaced revisit pass (t≈58–68 s, wrong ~1.3–1.5 m even AT
  loop-anchor keyframes — so NOT interpolation-between-anchors). **PGO spreads the end-of-drive
  correction smoothly along the stiff odom chain (odom var 1e-4 m²/edge vs loop var ≥0.015 m²)
  and cannot represent this drive's non-monotonic drift** (+0.5 m by 58 s reversing to −0.95 m by
  91 s). No loop subset fixes both buckets (ablated at thresh 0.3/0.15/0.10). Actionable for the
  team: the odom/loop variance ratio, not the loop detector, is the lever.
- Consequence for every vs-PGO number: silver truth is soft wherever drift was non-monotonic —
  and (frames-audit finding) **the truth floor on long-window sections is decimeter-scale**
  (submap accumulates over drifting odom inside its window while truth is the single correction
  at query ts; measured 0.116 m median / 0.328 m max inside a 30 s window). Accepted-answer
  medians ~0.24–0.32 m are near that floor — never quote them as precision. The 1 m/15° success
  bar stands. TOTAL_SPREAD (mixes all pairs) inherits the composition trap — worth telling the
  team. Latent dimos footgun found on the way (not affecting results): `Transform.__init__`
  silently replaces ts=0.0 with wall-clock time (Transform.py:56).

### Jul 17 night session — goal executed end-to-end (window 2, autonomous; ALL COMMITS LOCAL-ONLY per Aaryan's no-remote rule — push checklist below)

**The goal (confidence benchmark → priors-in-ICP → fiducial w/ confidence+age) is BUILT and RUN.
Every headline number independently re-derived by an adversarial verifier (exact match, 47/47
unit checks on the confidence math).** Full detail: `trial/harness/README.md`.

**Results (hk_village3, 120 kidnap sections, replay, PGO-silver truth; raw counts quoted because
at N=120 threshold conclusions swing on single samples):**
| config | success | risk @0.45 gate | risk≤2% gate exists? | median dt |
|---|---|---|---|---|
| ransac (today) | 77.5% (93/120) | 22.5% (27/120 accepted-wrong) | NO (best 1/45 @0.959) | 9.7 s |
| ransac+fiducial | 95.8% (115/120) | 4.2% (5/120) | yes (0.911, cov 105/120) | 11.0 s |
| fiducial+judge | 95.0% (114/120; 3 no-marker = failures) | 2.6% (3/117) | ~ (0.82, cov 0.88) | **0.4 s** |
- All 27 baseline failures are CONFIDENT busts (fitness 0.81–0.96, rotations 60–160° — the
  yaw-flip class); the docs' 0.6 gate changes nothing (risk 22.7%); the run's HIGHEST-fitness
  answer (0.995) is wrong by 2.9 m/157°. The 0.45-vs-0.6 debate is moot — measured.
- Live-stack note: all failures are sub-50k-pt submaps — MIN_LOCAL_POINTS is what protects the
  robot today, not fitness. fiducial+judge = lesh's "RANSAC stands down" case: ~24× cheaper.
- ~~china_office marker revisit (n=3,708, 9 ids): PointLIO already 3–10 cm consistent at 5–18 min
  gaps; offline PGO on top DEGRADES it 10–100×.~~ **EXCLUDED (Aaryan, Jul 17 morning: "don't use
  china office recording") — do not cite china_office-derived numbers anywhere.** The
  per-recording-truth principle stands on village3 alone: **"PGO = silver truth" is a
  per-recording claim — qualify it with the marker revisit test.** The production-lane version of
  this measurement should come from `recording_go2_mid360_...` (lesh's purpose-built walk, lane B)
  instead — experiment scaffolded, paused.
- **Adversarial round 2 (circularity verifier, PARTIAL) — REFRAMING adopted for all claims:**
  - **Gate split is the decisive cut**: gate-reached sections (≥50k pts, n=50): ransac 50/50 —
    already 100%, zero improvement available. Gate-missed (n=70): 61.4% → 92.9%. The entire
    fiducial gain lands on attempts today's live stack REFUSES to make (MIN_LOCAL_POINTS skip).
    **Honest headline = coverage extension into the early/power-on regime, NOT accuracy fix.**
  - Circularity quantified: marker map shares truth's PoseGraph → 90/117 sections had a
    bar-passing candidate PRE-judge (median cand err 0.46 m ≈ tag scatter; max 1.29 m, inside
    ICP's basin by construction). Benchmark cannot detect "marker map wrong vs reality."
    Decorrelation next: temporal-split map · different-recording map · ages >30 s.
  - Failure taxonomy fixed: median err_r 84.8°, only 4/27 near 180° — say "large-rotation
    wrong-basin", not "yaw-flip". Scenario label: tracking-recovery/power-on-near-tag, NOT
    kidnap (ages ≤23.5 s, median 3.9 s; the 120 s age model + confidence decay unexercised —
    age degradation itself real, Spearman 0.50).
  - **Confidence-blindness finding STANDS and sharpens (circularity-proof, it's about ransac):**
    fitness stays confident-wrong 2–4% even in the fiducial arm — frame 98 picked a 2.92 m/157°
    ransac answer at fitness 0.995 OVER a 0.147 m fiducial candidate; frame 116 hit fitness 1.000
    on a 41°-wrong pose. The judge is source-blind and genuinely exercised (ransac won 89/117
    covered sections), but fitness saturation on sparse geometry is the enemy.
  - Real unclaimed win: fiducial+judge = 95.0% at 0.39 s median vs 9.7 s (25× cheaper) — the
    "markers visible → search stands down" case, same caveats.

**What landed where:**
- `trial/harness/` (this repo, committed): prep.py / run_bench.py / markers.py / china_markers.py
  / analyze.py / confidence.py + tests + README. Figures in `trial/results/figures/` (3).
- dimos branch `feat/fiducial-relocalization`, 2 LOCAL commits on top of a6be7e42e (NOT pushed):
  - `7647d63f2` FiducialPrior (age-decayed, toggleable `use_fiducial_prior`, `world_map_fix`
    stream Out→In wiring) + **fisheye bug fix**: `visual_relocalization.py` never passed
    `distortion_model` to PnP — Go2's equidistant coeffs misread as radtan (our own PR's code);
    regression test projects fisheye corners, requires <2 cm. 76/76 tests, mypy clean.
  - `fadb41e70` PR simplification per Aaryan: `dimos/mapping/benchmark/` devtool + CLI removed
    (2,610 of 4,218 inserted lines — built for the CUT real-life benchmark; zero remaining
    importers, verified). PR shrinks to its subject: 1,608 insertions / 10 files.
- `site/` (canonical) + `~/portfolio` clone (deploy vehicle, LOCAL commit): /dimensional rewritten
  to v5 + tonight's replay-verified numbers; `next build` + typecheck pass. NOT deployed.
- Linear (done BEFORE the no-remote instruction, per explicit ask): DIM-920 description rewritten
  (phases 1–3, fusion after), status comment posted, sub-issues DIM-1252/1253/1254 created (none
  marked done — untested then). Nothing touched since.

**Morning checklist (Aaryan):**
1. Review + push trial repo `main` (all local commits).
2. Review dimos branch commits `7647d63f2` + `fadb41e70` → push to fork → PR #3016 updates.
3. Portfolio: review local commit → `vercel --prod` (from `~/portfolio` on this box, or laptop).
4. Linear: paste bench results comment on DIM-920 (draft in §5-adjacent block below); mark
   DIM-1252 (Phase 1) as its verification runs are now real; DIM-1253 stays open until fiducial
   verified on-robot or cross-run.
5. Rotate the Linear API key when convenient (it transited chat in plaintext).

**Ready-to-paste DIM-920 comment (final draft — adversarially verified wording, post in morning):**
> Offline benchmark results (replay, hk_village3, 120 sections, deterministic seeds, truth =
> PGO-corrected poses with the noise floor measured; harness in the trial repo; every number
> independently re-derived by an adversarial verification pass):
>
> **Confidence finding (the DIM-940/#2137 "match confidence" question, answered with data):**
> today's RANSAC→judge publishes fitness that does NOT gate safely on sparse submaps — 27/120
> answers are >1 m/15° wrong at fitness 0.81–0.96 (the run's HIGHEST-fitness answer, 0.995, is
> 2.9 m/157° off), no threshold reaches 2% false-accept, and the code-vs-docs gate debate
> (0.45 vs 0.6) is moot: both give ~22.5% risk. On gate-passing (≥50k pt) submaps it's 50/50 —
> fitness saturation on sparse geometry is the specific enemy. Submap size belongs in the
> confidence reading.
>
> **Fiducial prior (toggleable, age-decayed, same judge — no bypass):** extends reliable
> relocalization into exactly the regime the live stack currently refuses (MIN_LOCAL_POINTS
> skip): small-submap sections 61.4% → 92.9% (gate-passing were already 100% without markers).
> Markers-only + judge: 95.0% at 0.39 s median vs 9.7 s (~25× cheaper — the "RANSAC stands down
> when tags are visible" case). Honesty caveats: the marker map derives from the same PGO run as
> truth (deployment-realistic but truth-correlated — 75% of sections had a bar-passing candidate
> pre-judge); scenario is tracking-recovery/power-on-near-tag, not kidnap; decorrelation tests
> (temporal-split map, different-recording map) are next.
>
> **PGO-as-truth qualifier (leshy's observe→loop→observe test, stratified):** village3: PGO
> reconciles loop-return drift (0.93→0.28 m) but misplaces one revisit pass ~1.4 m even at
> loop-anchor keyframes — ablation-verified mechanism: stiff odom variance (1e-4 m²/edge vs
> ≥0.015 m² loops) spreads the end-of-drive correction where drift didn't occur; non-monotonic
> drift is unrepresentable. Wherever PGO poses are treated as ground truth, this per-recording
> check is worth running; the odom/loop variance ratio looks like the lever.

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
- **Jul 17 (later) — PR #3016 supersedes #2808, name resolved.** Aaryan confirmed fork-and-pull
  as standing practice ("if it is then keep doing this") but wanted the branch name right:
  `feat/fiducial-relocalization` pushed to the fork as a new branch (no rename — renames close
  cross-repo PRs), successor **PR #3016** opened from it (same 16 commits, title now includes the
  priors work), then #2808 closed with pointer comments both ways. Never a moment without a live
  PR. CUDA window active in parallel (task 0 done; LFS-assets blocker found + fixed on the board).
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

**Jul 17 (1:44–2:25 AM) — lesh (Discord, Aaryan asked "do we have proof anywhere of PGO being
accurate?"):**
- **PGO verification protocol = the marker revisit test**: observe the marker, do a very large
  walk, come back, observe again — PGO should make the marker locations overlap. (Confirms the
  method this window implemented independently the same night.)
- `dimos map global hk_building_all_around --pgo` = the large-walk example (no markers, but at
  that scale drift is visually obvious; "PGO fails are usually very obvious"). Nearly impossible
  data for Go2 lidar (outdoors, pedestrians) yet the pipeline does well on it.
- **"We have mid360 scans of long walks then staring at markers for that reason"** — purpose-built
  production-lane verification recordings EXIST. LFS catalog candidates (checked same night):
  `recording_go2_mid360_2026-05-29_4-45pm-PST.db` (+`_corrected` twin), `markers_go2.db`,
  `go2_mid360_stairs.db`, `hk_building_{all_around,elevator,enterance,park}.db`.
- Rerun tip: with `--pgo` turn off the raw-map layer — it renders raw and PGO maps overlapped.
- Realtime PGO: nice-to-have, "algo runs 30x realtime so it's easy," just not done because of
  Go2 downprioritization — "last thing worth investing in though, it's a one day job."
- Production platforms carry better lidars — **mid360 focus for now** ("it's everywhere"); recent
  research may eventually fold Go2 into the "serious lidar processing" codebase via good raw
  Go2 lidar data.
- **Both PGO and relocalization algos are Go2-specific** (WebRTC data path) — not transferable to
  other platforms.

Same-night data note vs lesh's "PGO fails are usually very obvious": the revisit test at scale
found two NON-obvious classes — village3's ~1.4 m misplaced revisit pass (map overlay looks
fine; stiff-odom-variance artifact) and a second class seen on china_office (EXCLUDED as
evidence per Aaryan Jul 17 — awaiting the purpose-built mid360 walk instead). Failures of this
kind only show against a physical marker anchor — which is
presumably exactly why the team records marker-staring walks.

**Jul 17 — PGO ACCURACY EVIDENCE SWEEP (repo + Linear read-only; answers Aaryan's "does
anything show PGO better than ~0.3 m?"): NO.** Nothing anywhere measures dimos PGO against
external ground truth. Repo: shipping docs disclaim loop closure (deep_dive.md:63); best prior
measurement = never-merged comparison report, tag spread 2.0->0.40 m (flagship) — consistent with
our 0.28-0.35 m; the autoresearch tuning branch's own author wrote TOTAL_SPREAD "only tells you
when corrections diverge — not whether they converge to the right place", measured ~36%
UNDER-correction, and asked for ground truth that didn't exist. All sub-0.3 m instances are
artifacts (near-zero-drift recording / artificial drift / same-recording self-reloc / KITTI
ATE~0 with GT fed as odometry). #2137's "PGO-corrected groundtruth" = assumption, never
quantified. Linear: only sub-0.3 m figures are FAST-LIO2 literature specs in a drone PROPOSAL
(DIM-1088, "Estimated Value"); reloc thresholds elsewhere are placeholders. **Conclusion: the
marker-revisit numbers from this trial are the first externally-anchored PGO accuracy evidence
at Dimensional, and prior internal (unmerged) work independently lands in the same range.**

**Jul 17 — GRAVITY WALKOVER FIXED (dimos branch, local commit; found->fixed->verified same
day by the benchmark loop):** `refine_candidates` gains `sources=` — per-source gravity gating
(each source falls back to its own tilted pool only if IT has no upright member; default path
bit-identical). Regression test reproduces the walkover under the old gate and asserts the fix
(10/10 suite green, mypy clean). Field re-run on the fixed code: v1 lastpose 0.625->0.708, v5
0.792->0.875, ZERO lastpose-source failures remain on either. FiducialPrior inherits the
protection (all priors flow through the per-source gate). This is the benchmark's development
loop closing end-to-end: benchmark finds -> probe isolates -> fix lands -> benchmark confirms.

**Jul 17 — DECORRELATED FIDUCIAL VERDICT (the goal's last open question — HOLDS; mid360 walk,
n=40 full denominator, replay):** referee tag 4 verifiably ABSENT from all 45 fixes (ids
{0,1,2,6,7}). ransac 52.5% (21/40; hard 2.78M-pt outdoor premap, 62.5 s solves, wrong-basin
busts 6.7–72 m) -> ransac+fiducial **72.5% (+20.0)**; covered sections 7/15 -> 15/15 while ALL 25
uncovered sections returned BYTE-IDENTICAL errors — determinism proves the whole gain is the
marker fixes. Rescues were catastrophic->5-10 cm. fiducial+judge: 100% of attempted at ~9x
faster; 37.5% coverage = the limiter (markers help where markers are). Per covered section the
decorrelated effect is STRONGER than v3's truth-correlated one. v3's 95.8% formally superseded
(its fixes are empty under the split — its only tag IS the referee): **72.5% decorrelated is THE
fiducial number.** Residual caveats: frame-level PGO correlation (inherent to silver truth;
referee-scoring of saved T_est = follow-on), n=40, one recording.

**Jul 17 — GRAVITY-GATE WALKOVER (design bug found by the benchmark, probed bit-exactly; fix
queued behind running jobs):** refine_candidates' gravity fallback (`upright if upright else all`)
is pool-global — one near-upright SEED un-empties `upright` and discards the ENTIRE all-tilted
RANSAC pool, so the stale seed wins by walkover. Measured: lastpose flag destroys wins on v1/v5
(56/72 -> 53/72 aggregate, zero rescues; one walkover bust PASSES the 0.45 gate at 0.586 — a live
confident bust CREATED by the flag). CORRECTIONS: v3 "seed entrenchment" was outcome-NEUTRAL
(relabeled existing failures); FiducialPrior has identical exposure (near-upright fixes) — Phase-2
red flag caught pre-live. FIX (after suite + mid360 workflow finish): per-source gravity gating
(each source falls back to its own tilted pool only if IT has no upright member) + regression test
from probe_gravity_walkover.py (repro: hk_village1 frame 831). Caveat: 3/72 rate is at benchmark
stride (~40 frames between sections; seed maximally stale) — mechanism stride-independent, rate
may differ live.

**Jul 17 — MID360 DECORRELATED BENCHMARK (replay, 40 sections, PGO-silver truth + tag-4 referee;
results jsons in harness out/, instrument `trial/harness/referee_verdict.py`, commit 621b92a):**
- ransac 52.5% (21/40) -> ransac+fiducial 72.5% (29/40) -> fiducial+judge 37.5% (15/40 full
  denominator; all 15 fix-covered sections succeed, median 6.8 s vs ransac's ~56-62 s). All 8
  flips F->S (0 regressions), errors 21-72 m -> 0.05-0.10 m, judge fitness 0.94-0.99 — each flip
  a MERIT win (fiducial fitness > ransac's counterfactual from the ransac-only run, so the
  walkover bug above cannot explain them; per-source attribution 28/12 may shift post-fix).
- **Referee verdict (the decorrelation answer): the within-section test is EMPTY on this walk** —
  4/40 sections saw tag 4 in-window (159/313 start, 5869/6024 end), NONE carry fiducial fixes
  (referee bookends the walk; fiducials live mid-walk; nearest miss 6.3 s). Both arms bit-identical
  there (|dT|=0). What the referee DOES certify: T_true agrees with consensus 0.06-0.26 m at all 4
  (truth is hard exactly where the referee watches; PGO rebuild wobble there 1-7 cm vs up to
  1.8 m mid-walk — measured pkl-vs-bak); it confirms all 4 PGO verdicts incl. convicting frame
  159's confident bust (fitness 0.58 > gate, 26 m wrong, referee sees 25.2 m). vs village3
  77.5->95.8: the mid-walk gain stays truth-correlated (44/45 candidates in-bar pre-judge, same-
  graph map — same circularity class); the referee-ID split decorrelates the INSTRUMENT, not the
  fixes. True decorrelation still needs temporal-split/cross-recording maps or referee-covisible
  fiducials (deployment note: place a fiducial in the referee's view field).
- Ops: two sessions raced the same result files (~12:29-12:36); deterministic fields verified
  equal across writes, dt/wall contended (ransac+fiducial's 113.7 s median is ~2x inflated).

**Jul 17 — CROSS-RECORDING CONFIDENCE (pooled, N=232, triple-verified, commit 35cff6c):**
- **The design conclusion for the runtime track: fitness ranks correctness well WITHIN an
  environment (AUROC v6 1.000, office 1.000, v5 0.94, v1 0.92, v3 0.84) but thresholds do NOT
  transfer across environments** (bust ranges: villages 0.62–0.995 · office 0.53–0.75 ·
  all_around 0.32–0.66). A gate zeroing office risk (>0.752) still admits 34 village failures;
  zeroing village risk needs >0.995. **Per-environment calibration (or covariate-carrying
  confidence) is a requirement, not an option.**
- Pooled: 78.0% success; risk @0.45 = 22.0% (51/232); ≤2%-risk gate exists at 0.942 but costs
  40% coverage and still passes v3's 0.995 bust. Gate-split: ≥50k pts 95.4% vs 62.9% below —
  BUT office is gate-insensitive (81% vs 79%): submap size explains outdoor failures, not indoor
  wrong-rooms. Two failure regimes, now separated.
- CORRECTION (verifier-caught): v3 busts are 0.62–0.995 median 0.90 on the full-denominator
  120-section results — the earlier "0.81–0.96" phrasing came from the first 24-section run.
- all_around joined the suite measured: 83.3% (20/24), 0.028 m median, 24.6 s solves on the
  1.4M-pt premap; failures 0.32–0.66 — first recording where the 0.45 gate mostly works.
- Figure: `trial/results/figures/confidence_cross_recording.png` (5 per-recording risk–coverage
  curves, one axes).

**Jul 17 morning — lesh's named instruments, executed (villages + hk_building_all_around;
china_office excluded per Aaryan):**
- **Marker-revisit across ALL SIX villages** (672 sightings; spatial-cluster validity check run
  per village): v1/v3/v5/v6 carry a single physical tag (stats valid); **v2 and v4 have MULTIPLE
  physical tags sharing id 10** (3 and 2 cm-tight clusters, 2.4–4.8 m apart — naive same-id
  revisit stats invalid there; excluded from the verdict).
- **Verdict on lesh's protocol, Go2 lane, 4 valid recordings — 60 s+ loop-return gaps (median
  raw→PGO, m):** v1 0.600→0.353 · v3 0.934→0.280 · v5 0.368→0.345 · v6 0.674→0.150. **PGO
  improves loop-return agreement in 3/4, neutral in 1, never worse at this gap — his claim HOLDS
  at the protocol's intended case.** Mid-gap (10–60 s) is hit-or-miss (v6 better, v1 mixed,
  v3/v5 worse — the stiff-chain spread artifact is common but not universal). Policy unchanged:
  qualify PGO truth per recording via this test.
- **Duplicate-id lesson for DIM-920/fiducial prior:** same-id physical tags poison a marker map
  and would poison fiducial fixes (wrong-room class reintroduced). Deployment rule: unique ids
  per space (or multi-hypothesis handling in the prior). The harness now detects this
  (cluster check) before trusting any marker map.
- **hk_building_all_around** (`--pgo` rrd written; profile figure + JSON): 192 m walk, 231 s,
  441 keyframes, only 3 loop closures (all at the return-to-start); PGO's own correction:
  median 1.01 m, max 1.45 m (~0.75% of path). At this scale drift IS eyeball-visible in the
  raw-vs-PGO overlay — lesh's point stands for LARGE walks; the marker test remains the
  instrument for sub-meter failures. Open: `rerun dimos/hk_building_all_around.rrd`.

Same-night follow-through on lesh's pointers (window 2):
- Pulled `markers_go2.db` (0.33 GB) — **unusable for PGO: 0/795 lidar obs carry a real pose**
  (detector-dev recording, likely #2044-era). Noted, set aside.
- Pulled `recording_go2_mid360_2026-05-29_4-45pm-PST.db` (3.3 GB) — **the purpose-built one**:
  13-min, >100 m walk, 6 tag ids each revisited at gaps up to ~12 min, and BOTH lidar chains in
  one file (`lidar` go2-WebRTC + `fastlio_lidar` mid360/FAST-LIO, both fully posed; fastlio scans
  ~15× denser: 3,926 vs 265 pts first frame; the two 'world' frames are independent). Two-lane
  marker-revisit experiment (`trial/harness/two_lane_markers.py`): same walk, same tags, same
  pixels — lesh's verification on the Go2 lane AND the production lane simultaneously. RUNNING
  as of this write; results land below when done.

Acted on (same night): all benchmark numbers re-labeled by lane (village3 = Go2 lane, explicitly
non-transferable per lesh; the production-lane measurement must come from the purpose-built
mid360 walk recording — china_office numbers EXCLUDED per Aaryan Jul 17); the trial's value story reframed
around the transferable layer (priors/judge architecture, camera-based fiducial prior, confidence
-benchmark methodology, marker-revisit PGO qualifier — all embodiment-agnostic, answering
DIM-944's embodiment complaint); age-decay constants noted as per-lane knobs (near-drift-free
PointLIO ages marker fixes slowly — Phase-4 autoresearch tunes per embodiment). Realtime PGO:
never proposed, nothing to unwind. DIM-920 draft comment updated with the lane note.

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
