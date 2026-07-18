// /dimensional (unlisted trial hub) — public, shared on Dimensional's Discord.
// All page data lives here so an update = edit this file + `vercel --prod`.
// PUBLIC-SAFETY: additive framing only. No critique of dimos, no internal/
// Discord/roadmap references, no unverified % claims. This is what Aaryan is
// building, framed positively and generous to the existing system.
// Every number on this page is replay-verified (real recorded drives, offline)
// and labeled with its truth source. Nothing here is simulated unless it says so.

export const trial = {
  kicker: "Dimensional FDE Trial · Aaryan Agrawal · July 15 – July 24",
  title: "Relocalization: a Universal Confidence Reading + a Fiducial Prior",
  objective:
    "Main Objective: relocalization that knows how much to trust itself — one confidence reading across all methods, and a marker prior where it helps most",
  deliverables: [
    "Universal confidence reading across pluggable relocalization priors",
    "Fiducial (AprilTag) prior — toggleable, age-aware, never bypasses the judge",
    "Offline confidence benchmark on recorded drives (replay, deterministic)",
    "Fusion of relocalization priors (next, after this ticket)",
  ],
  pr: {
    href: "https://github.com/dimensionalOS/dimos/pull/3016",
    label: "PR #3016",
  },
  lastUpdated: "July 17, 2026",
};

export type Status = "active" | "done" | "pending";

export type FlowStep = {
  label: string;
  tint?: "amber" | "green";
  image?: { src: string; alt: string };
};

// How one benchmark section is scored — the offline replay flow.
export const runFlow: FlowStep[] = [
  { label: "Cut a section from a real recorded drive" },
  { label: "Accumulate its lidar submap (live-mapper settings)" },
  { label: "Priors propose candidates: RANSAC · last-pose · marker", tint: "amber" },
  { label: "ONE shared judge ranks all candidates on geometry" },
  { label: "Score vs PGO truth + marker agreement", tint: "green" },
];
export const runFlowNote =
  "replay of real sensor data — same drive, bit-identical input for every config; deterministic per-frame seeds";

export const baselines = [
  { label: "ransac", detail: "today's global search" },
  { label: "+ fiducial prior", detail: "this work" },
  { label: "fiducial + judge", detail: "markers visible → search stands down" },
];

// Headline figure: published confidence vs actual error, per attempt.
export const oneRunFigure = {
  src: "/dimensional/confidence_fitness_vs_error.png",
  alt: "Scatter of published fitness vs translation error against PGO truth, per relocalization attempt, replay",
  caption:
    "Does the published confidence predict correctness? Every attempt on a real recorded drive (replay; truth = PGO, ±noise floor measured)",
};

export const metrics: { name: string; note?: string }[] = [
  { name: "Success rate", note: "<1 m and <15° vs PGO truth — full denominator, no excluded frames" },
  { name: "Risk–coverage", note: "false-accept rate at every possible accept threshold" },
  { name: "Confidence quality", note: "AUROC + calibration — does the score rank correctness?" },
  { name: "Marker agreement", note: "a physical tag never moves — truth PGO can't fake" },
  { name: "Time-to-fix", note: "median seconds per accepted answer" },
];

// Phase 1 — universal confidence reading (priors + one judge).
export const phase1 = {
  status: "done" as Status,
  title: "Phase 1: Universal confidence reading",
  objective:
    "Objective: every relocalization answer carries one comparable confidence — pluggable priors propose, one shared fine-ICP judge scores them all, and the winning source is published with the pose.",
  code: "dimos/mapping/relocalization/priors.py — Candidate · RelocPrior · relocalize_with_priors()",
};

// Phase 2 — the fiducial prior.
export const phase2 = {
  status: "done" as Status,
  title: "Phase 2: Fiducial prior",
  objective:
    "Objective: a marker sighting proposes a high-confidence pose candidate into the same judge — toggleable, age-decayed, and it still has to win on geometry like every other candidate.",
  lines: [
    "FiducialPrior — age-gated marker fixes into the shared judge (PR #3016)",
    "Decorrelated result (the referee tag never helps, only grades): 52.5% → 72.5% on a hard 100 m outdoor walk (n=40, full denominator, replay) — sections with marker coverage went 7/15 → 15/15 while all 25 uncovered sections returned byte-identical answers, proving the gain is the markers",
    "Rescues were catastrophic-to-centimeters: 6.7–72 m wrong-basin solves pulled to 0.05–0.10 m",
    "Markers visible → the global search can stand down: 100% of attempted at ~9× faster on that walk; coverage (not accuracy) is the limiter",
  ],
  demo: {
    src: "/dimensional/confidence_risk_coverage.png",
    alt: "Risk-coverage curves: false-accept rate vs coverage for each configuration, replay",
    caption:
      "The accept threshold, chosen from data: risk (accepted-but-wrong rate) vs coverage per configuration (replay, 120 sections)",
  },
  codeLead: "dimos/mapping/relocalization + dimos/perception/fiducial",
  codeClass: "FiducialPrior · VisualRelocalizationModule",
};

// Phase 3 — the offline confidence benchmark.
export const phase3 = {
  status: "active" as Status,
  title: "Phase 3: Offline confidence benchmark",
  objective:
    "Objective: measure, on recorded drives with deterministic seeds, whether the published confidence predicts correctness — and pick accept thresholds from risk–coverage curves instead of folklore. Truth honesty built in: PGO's own noise floor is measured (marker revisit test) and every number carries its truth label.",
};

// Phase 4 — fusion of relocalization priors (after this ticket).
export const phase4 = {
  status: "pending" as Status,
  title: "Phase 4: Fusion of relocalization priors",
  objective:
    "Objective: confidence- and age-weighted arbitration over parallel, toggleable sources — one owner of the world→map correction, degrading gracefully as sources come and go.",
  nodes: ["lidar prior", "fiducial prior", "fused correction"],
  line: "sources plug in; an arbiter owns the correction; a healthy source always carries the pose.",
  tree: `dimos/mapping/relocalization/
  priors.py  # Candidate · RelocPrior · RansacPrior · LastPosePrior · FiducialPrior
  relocalize.py  # candidate generation + the ONE shared judge
  module.py  # live module — confidence + winning source published
  fusion.py  # next: the arbiter (after this ticket)`,
};

// Evidence log — figures with explanations, appended as results land.
// Populated Jul 17; every number replay-verified, truth source labeled.
export type EvidenceFigure = { src: string; title: string; explanation: string };
export type EvidenceSection = { heading: string; intro: string; figures: EvidenceFigure[] };
export const evidence: EvidenceSection[] = [
  {
    "heading": "The relocalization benchmark (development-time, marker-truth)",
    "intro": "The recordings Dimensional already collects include printed AprilTags on walls — and a tag bolted to the world cannot move. So when a localization system places the same physical tag in different spots across repeated sightings, the scatter is that system's own error. That is the whole instrument: external truth, independent of the lidar pipeline it grades, usable offline at development time. The standing setup (v1): each recording designates one referee tag used only for scoring — it never enters a marker map and never produces a fiducial fix — while every other tag id stays available to the runtime fiducial track, and a spatial-cluster validity check must pass before any id is trusted as a referee (it disqualified two recordings — see the exclusion figure). The current suite is seven recordings: four villages and a purpose-built 13-minute multi-tag walk carry marker referees, and two recordings verified tag-free (checked frame by frame) serve as drift-profile runs graded on PGO's own consistency. Every number below is replay of a real recorded drive — real sensor data played back through the real stack, deterministic seeds, full denominators, never simulation — and the truth source is the physical tag itself (each system scored on the self-consistency of its own placements), except where a figure is explicitly labeled PGO self-consistency. This benchmark is the development-time referee that calibrates the runtime confidence track in the next section.",
    "figures": [
      {
        "title": "Why a physical tag is a referee: one tag, 156 sightings, raw vs PGO",
        "explanation": "One physical AprilTag in hk_village3, sighted 156 times across a 102-second drive (replay; the tag is the truth source — it cannot move, so scatter in where the robot places it is pose error). Left: placements computed from raw odometry drift apart as the drive goes on. Right: after PGO correction, three passes agree to 0.28 m at loop-return gaps of 60 s and more (0.93 m on raw odometry) — the loop-closure job, done. But the revisit pass around t≈60 s lands ~1.4 m away after correction: the optimizer spreads the end-of-drive fix smoothly along the whole trajectory, and this particular drive's drift was non-monotonic — an error the raw-vs-PGO map overlay does not show, which is exactly the class of failure this marker instrument exists to catch, and it comes with an actionable lever (the odometry-vs-loop edge-variance ratio).",
        "src": "/dimensional/pgo_marker_explainer.png"
      },
      {
        "src": "/dimensional/rerun_pgo_marker_explainer_screenshot.png",
        "title": "The same evidence, live in the team's 3D viewer (rerun)",
        "explanation": "Everything above is also inspectable in 3D: every benchmark recording ships a rerun verification file with toggleable layers. Gray line = the raw odometry trajectory; blue line = the PGO-corrected one. The small dots are the tag sightings (color = time into the drive) in two switchable versions — raw vs corrected — so flipping between them shows the drift being removed. The globes are the conclusions drawn to scale: the green globe sits where three separate passes agree the tag is, and its radius IS the measured agreement (0.28 m); the red globe marks the pass the optimizer displaced, 1.4 m from consensus — the gap between the two globes is the finding. Dots are evidence, globes are conclusions; anyone can orbit the scene and check one against the other."
      },
      {
        "title": "One referee tag, three systems — the cross-village benchmark chart",
        "explanation": "The headline chart: in each valid village recording, the same physical referee tag is placed by three systems — raw odometry, PGO-corrected poses, and the relocalization module solving each benchmark section from scratch — and each bar is the median deviation of that system's own placement cloud from its own centroid (replay; self-consistency against the physical tag, no shared frame between systems). PGO's clearest aggregate win is village6 (0.34 m → 0.13 m); stratified by revisit gap, PGO improves 60 s+ loop-return agreement in 3 of 4 valid villages and is never worse at that gap (medians: v1 0.60→0.35 m, v3 0.93→0.28 m, v5 0.37→0.35 m, v6 0.67→0.15 m) — aggregate bars mix short-gap sightings, so read them next to the per-village panels. The module lane scatters wider here (medians 0.32–1.83 m): these short outdoor drives keep the live submap small and sparse, and about half of these attempts (100 of the 192 village sections) sit below the live robot's 50k-point size gate — the benchmark scores them anyway, full denominator, because that refused regime is precisely where the fiducial prior extends coverage.",
        "src": "/dimensional/benchmark_odom_pgo_module.png"
      },
      {
        "title": "hk_village1 — each system's own placements of the referee tag",
        "explanation": "Three panels, one per system, each centered on that system's own centroid; the dotted circle is 0.5 m (replay; truth source = the physical tag, self-consistency per system). Raw odometry over this short drive holds 0.24 m median deviation (n=151 sightings); PGO holds 0.22 m overall and pulls the loop-return passes together (60 s+ pairs: 0.60 m raw → 0.35 m, median). The module's per-section answers (n=310 placements) fall into a few distinct clusters spread across roughly 4 m, median deviation 1.83 m — global search on a sparse outdoor submap sometimes settles in a neighboring basin, the exact behavior the runtime confidence score has to flag, and the case a uniquely-identified marker resolves outright.",
        "src": "/dimensional/benchmark_hk_village1.png"
      },
      {
        "title": "hk_village3 — the reference recording, per-system tag placements",
        "explanation": "The most-instrumented recording — the same one the confidence harness ran 120 sections on. The referee tag was sighted 156 times: raw odometry places it at 0.07 m median deviation, PGO at 0.22 m — and the aggregate hides that PGO's improvement is concentrated exactly where its job is (60 s+ loop-return pairs: 0.93 m raw → 0.28 m), while one mid-drive revisit pass is moved ~1.4 m (see the explainer figure above). The module cloud (n=4810 placements from its section answers, 0.69 m median deviation) has a tight core with outliers meters out — those outliers are the same confidently-wrong answers the confidence section counts one by one. Replay; the physical tag is the only truth in the plot.",
        "src": "/dimensional/benchmark_hk_village3.png"
      },
      {
        "title": "hk_village5 — the neutral case for PGO at loop returns",
        "explanation": "Raw odometry 0.09 m median deviation (n=111 sightings), PGO 0.17 m, module 0.84 m (n=412 placements) — replay, self-consistency against the physical tag. This is the neutral village in the stratified read: 60 s+ loop-return pairs go 0.37 m raw → 0.35 m corrected (median) — little drift to fix, nothing made worse. The module's placements form a main cluster plus two distant basins on this sparse outdoor submap regime — one more data point for why submap size belongs in the published confidence reading.",
        "src": "/dimensional/benchmark_hk_village5.png"
      },
      {
        "title": "hk_village6 — PGO's clearest win on the referee tag",
        "explanation": "The village where the aggregate and the stratified read agree loudly: raw odometry 0.34 m median deviation (n=91 sightings) → PGO 0.13 m, and at 60 s+ loop-return gaps 0.67 m → 0.15 m (median) — the largest relative tightening of the four valid villages, about 4.5×. The module lane also posts its best village number here (0.32 m median, n=254 placements), with one distinct wrong basin about 3 m out. Replay; each panel is that system's own placement cloud around its own centroid, the physical tag as referee.",
        "src": "/dimensional/benchmark_hk_village6.png"
      },
      {
        "title": "Why villages 2 and 4 are excluded — one id, several physical tags",
        "explanation": "The validity gate that keeps the benchmark honest. A spatial-cluster check (single-linkage, 1.0 m) on every tag id found that in hk_village2 \"id 10\" is three different physical tags (sighting clusters of n=44, n=30, and n=16, each 2.4–4.8 m from its nearest neighbor) and in hk_village4 it is two (n=23 and n=50, over 2 m apart) — same-id revisit statistics there would compare different objects, so both recordings are excluded from every number above (replay, raw-odometry positions shown). This doubles as a deployment rule for the fiducial track: tag ids must be unique per space — or handled multi-hypothesis — before a marker map or fiducial prior can be trusted, and the harness now runs this check automatically before trusting any id.",
        "src": "/dimensional/benchmark_excluded_duplicate_ids.png"
      },
      {
        "title": "192 m outdoor walk, no tags — PGO's own correction profile",
        "explanation": "The no-marker complement to the tag benchmark, on a recording verified tag-free (0 detections in 663 sampled frames) — so this figure is labeled what it is: PGO self-consistency, not independent truth. A 192 m, 231-second walk around a building, replayed: left, raw odometry vs the PGO trajectory; right, the correction magnitude PGO applied along the drive — median 1.01 m, peaking at 1.45 m (about 0.75% of path length), anchored by 3 loop closures at the return to start. At this scale drift is visible to the eye in the overlay, which is what a profile like this is for; the marker instrument above exists for the failures an overlay cannot show — sub-meter drift and the misplaced-pass class.",
        "src": "/dimensional/hk_building_all_around_pgo_profile.png"
      },
      {
        "title": "go2_hongkong_office — drift profile of the indoor eval map",
        "explanation": "The same profile instrument on the 186 m indoor office recording that serves as the second environment for the confidence harness — again labeled PGO self-consistency, not independent truth (replay; verified tag-free, 0 detections in 398 sampled frames, so no marker referee exists here). Across the 9.2-minute drive PGO finds 40 loop closures, and the correction it applies to raw odometry climbs to ~2.3 m by the end. The PGO-corrected trajectory from exactly this run is the silver truth the office confidence numbers are scored against — silver because PGO rebuilds wobble by a measured ~6 cm run to run — which is why those numbers quote raw counts and read their centimeter-scale medians as agreement with PGO, not absolute accuracy.",
        "src": "/dimensional/go2_hongkong_office_pgo_profile.png"
      }
    ]
  },
  {
    "heading": "Relocalization confidence (runtime)",
    "intro": "The runtime track. Every relocalization answer carries one comparable confidence score: pluggable priors propose pose candidates — today's RANSAC global search, a last-accepted-pose seed, and the fiducial (marker) prior — and one shared judge scores every candidate on geometry alone, publishing the winning pose with its score and its source. Nothing bypasses the judge; a marker candidate must win on measured fit like any other. This track runs in real time on the robot; the marker benchmark above is its offline referee. The question measured here: does the published confidence actually predict correctness? On hk_village3 (replay, 120 sections each solved with no initial pose, truth = PGO-corrected poses labeled silver with their ~6 cm rebuild wobble counted): today's configuration succeeds 77.5% (93/120), and all 27 wrong answers pass both accept thresholds in circulation. Adding the fiducial prior lifts it to 95.8% (115/120) — with the entire gain on sections below the live 50k-point submap gate (43/70 → 65/70, 61.4% → 92.9%; gate-reached sections were already 50/50 without markers) — so the honest headline is coverage extension into the power-on / tracking-recovery regime, plus roughly 25× less compute when tags are visible (median solve drops from ~10 s to 0.4 s), not an accuracy fix of behavior the live gate already protects. Fresh second environment — go2_hongkong_office (replay, 850k-point premap, PGO-silver truth from the profile above): RANSAC succeeds 80% (32/40, full denominator), median error across its 40 answers 0.033 m — centimeter agreement with the silver truth on the 32 correct ones, 7.8–21.7 m off on the 8 wrong ones — at a 41 s median solve at this map scale (offline workstation, not an onboard number). All 8 failures pass the 0.45 accept gate at fitness 0.53–0.75, and 3 of them sit above the 50k-point size gate — wrong-room matches the gate would have published — so the village3 pattern where submap size catches every failure is environment-dependent: one more measured reason the confidence reading needs more signals than fitness alone. One honesty note on the fiducial arm: its marker map is surveyed from the same recording that supplies the truth (deployment-realistic, but truth-correlated), so fiducial numbers read as coverage evidence; decorrelating the marker map from the truth recording is the named next step.",
    "figures": [
      {
        "title": "Published fitness vs true error — where accepted-but-wrong lives",
        "explanation": "Each point is one section's published confidence (the stage-2 wall-ICP fitness) against its translation error vs PGO silver truth, log scale, four prior configurations overlaid (replay, hk_village3, 120 sections per configuration, ±6 cm truth floor; dimos revision and seeds printed in the footer). The two vertical lines are the two accept thresholds in circulation, 0.45 and 0.60 — and every accepted-but-wrong answer clears both: the baseline's 27 wrong answers carry fitness 0.62–0.995, and the single highest-fitness answer of the entire run (0.995) is 2.9 m and 157° off. Correct and wrong answers overlap heavily on the fitness axis, so no fitness-only threshold separates them — the threshold debate is answered by measurement, and the chart shows why the confidence reading needs more inputs than fitness (on this recording, every baseline failure sat below the live 50k-point submap gate — submap size is the strongest missing signal).",
        "src": "/dimensional/confidence_fitness_vs_error.png"
      },
      {
        "title": "Risk vs coverage — what each accept threshold actually buys",
        "explanation": "The selective-prediction view: sweep the accept threshold and plot, for the answers you keep (coverage), the fraction wrong by more than 1 m/15° (risk); circles mark the 0.45 gate, squares the 0.60 gate (replay, hk_village3, PGO-silver truth, raw counts behind every point). For today's RANSAC configuration no operating point reaches 2% risk — the best available is 1 wrong among 45 kept (2.2%), at threshold 0.959 — and the two gates in circulation accept the same 27 wrong answers (risk 22.5% vs 22.7%): at N=120 the difference between them is zero. Adding the fiducial prior pulls risk down to 4.2% (5/120) at full coverage, and the markers-with-judge arm reaches 2.6% (3/117) while succeeding 95.0% overall (114/120, with 3 no-marker sections counted as failures). Fitness does rank correctness better than chance (AUROC 0.84 baseline, printed in the legend) — it just cannot gate safely on its own.",
        "src": "/dimensional/confidence_risk_coverage.png"
      },
      {
        "title": "Is fitness a probability? — reliability of the published score",
        "explanation": "A reliability diagram: within each fitness bin, the empirical success rate against the mean published fitness (replay, hk_village3, PGO-silver truth; the dashed diagonal is perfect calibration). For the baseline configuration the bin around fitness 0.65 succeeded 0% of the time (0 of 2) while the 0.95 bin succeeded 87% (84 of 96) — fitness orders answers usefully, but its value is not a probability (ECE 0.16), so treating 0.7 as \"70% sure\" would mislead any downstream consumer. The fiducial arms sit near the top with ECE 0.01–0.06 — mostly because their answers are mostly right, not because the score became calibrated. The practical consequence for the runtime track: publish the score together with its source and context (submap size, marker agreement) rather than reading it as a probability, and calibrate any future composite confidence against this benchmark before it gates anything on-robot.",
        "src": "/dimensional/confidence_reliability.png"
      },
      {
        "title": "One fitness gate, five environments — does the threshold transfer?",
        "explanation": "The deployment question a single-recording curve cannot answer: if one fitness threshold ships to every site, what risk does it buy? This figure pools the five full-denominator RANSAC runs — four villages plus go2_hongkong_office, 232 sections, 181 correct (78.0%) — and draws each recording's own risk-coverage curve (replay; truth = PGO silver, qualified per recording by the revisit test; every section counted, zero no-answer rows). Inside one environment fitness can rank answers perfectly — village6 and the office both reach AUROC 1.00, so some threshold separates all their right answers from all their wrong ones — but that threshold's value does not carry over: the office's 8 failures all sit at fitness 0.53–0.75 while the villages' 43 failures run 0.62–0.995, so the gate that zeroes office risk (just above 0.75) still admits 34 village failures, and zeroing village risk needs a gate above 0.995 that keeps almost nothing. Pooled, the 0.45 gate accepts every answer and is wrong 51 times in 232 (22.0%); reaching 2% pooled risk takes a 0.94 gate that keeps only 140 of 232 answers. Same conclusion as every chart above, now measured across environments: publish fitness with its context, and calibrate the gate per deployment against this benchmark rather than shipping one number.",
        "src": "/dimensional/confidence_cross_recording.png"
      }
    ]
  }
];

// Open questions — Aaryan's running list. ADD YOURS HERE: one string per
// question, newest at the top. Public page: keep wording additive/neutral.
export const openQuestions: string[] = [
  "Confidence calibration per environment: ship a per-space calibration artifact from the survey walk, or model the shift with covariates (submap size, sensor lane) in one global model?",
  "Premap freshness: when localization confidence is high, should well-localized scans write back into the map (content updates, frame frozen), and what confidence bar gates the pen?",
  "Fiducial deployment policy: enforce unique tag ids per space at install time, or teach the prior multi-hypothesis handling for duplicate ids?",
  "Online PGO: worth building now for the exploration lane, or after the mid360 fiducial-first runtime ships?",
  "Decorrelation at scale: record a fresh purpose-built walk (unique ids, referee at start/end, PointLIO aboard) to grow the benchmark set beyond one multi-tag recording?",
  "Marker map storage: stream-published for now vs a persistent K/V store under the map — when to switch?",
  "Where should the relocalization benchmark live long-term (own repo, docs page, CI job on recordings)?",
];

// The stack map — what existed, what this trial adds, what's open.
// Pill states: existed | added (this trial) | gap (designed, not built)
export type Pill = { t: string; s: "existed" | "added" | "gap" };
export type StackCol = { job: string; ask: string; live: Pill[]; dev: Pill[] };
export const stackMap: StackCol[] = [
  {
    job: "Odometry", ask: "where am I since boot?",
    live: [
      { t: "Go2 onboard odom", s: "existed" },
      { t: "FAST-LIO · mid360", s: "existed" },
      { t: "Point-LIO · mid360", s: "existed" },
    ],
    dev: [{ t: "lane accuracy measured: 8.8 m vs 2 cm / 13 min", s: "added" }],
  },
  {
    job: "Mapping", ask: "keep the map truthful",
    live: [
      { t: "VoxelGrid accumulate (drifts, forgets)", s: "existed" },
      { t: "LIO private map", s: "existed" },
      { t: "carve-merge view", s: "existed" },
      { t: "online loop closure", s: "gap" },
      { t: "gated map write-back", s: "gap" },
    ],
    dev: [
      { t: "PGO offline (gtsam + ICP)", s: "existed" },
      { t: "map global → premap export", s: "existed" },
      { t: "tag-constrained PGO (unmerged)", s: "existed" },
      { t: "marker-map export (bundle)", s: "added" },
      { t: "versioned map bundle + CI gate", s: "gap" },
    ],
  },
  {
    job: "Relocalization", ask: "find me on a known map",
    live: [
      { t: "RANSAC → ICP judge", s: "existed" },
      { t: "RelocalizationModule (2 s loop, 50k gate)", s: "existed" },
      { t: "VisualReloc (tags)", s: "existed" },
      { t: "pluggable priors · one judge, no bypass", s: "added" },
      { t: "FiducialPrior (age-aware)", s: "added" },
      { t: "per-source gravity fix", s: "added" },
    ],
    dev: [
      { t: "#2137 autoresearch tuning", s: "existed" },
      { t: "sections harness (kidnap replay)", s: "added" },
    ],
  },
  {
    job: "Confidence", ask: "how much to trust it?",
    live: [
      { t: "fitness ≥ 0.45 folklore", s: "existed" },
      { t: "universal confidence reading", s: "added" },
      { t: "per-environment calibration", s: "added" },
      { t: "health monitor (innovation, age)", s: "gap" },
    ],
    dev: [
      { t: "external-truth evidence: none before", s: "existed" },
      { t: "THE relocalization benchmark (marker referee)", s: "added" },
      { t: "risk–coverage → data-grounded gates", s: "added" },
    ],
  },
];
