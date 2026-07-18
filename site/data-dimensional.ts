// /dimensional (unlisted trial hub) — public, shared on Dimensional's Discord.
// All page data lives here so an update = edit this file + `vercel --prod`.
// PUBLIC-SAFETY: additive framing only. No critique of dimos, no internal/
// Discord/roadmap references, no unverified % claims. This is what Aaryan is
// building, framed positively and generous to the existing system.
// Every number on this page is replay-verified (real recorded drives, offline)
// and labeled with its truth source. Nothing here is simulated unless it says so.

export const trial = {
  kicker: "Dimensional FDE Trial · Aaryan Agrawal · July 15 – July 24",
  title: "Fiducial Relocalization",
  objective:
    "An ArUco prior composed into the existing real-time judge — benchmarked on recordings, validated IRL on go2 with and without mid360",
  deliverables: [
    "Fiducial (ArUco / AprilTag 36h11) prior — toggleable, age-aware, never bypasses the judge",
    "Universal confidence reading across pluggable relocalization priors",
    "Offline confidence benchmark on recorded drives (replay, deterministic)",
    "IRL validation on go2 — with and without livox mid360 (in progress)",
    "Fusion of relocalization priors (next, after this ticket)",
  ],
  pr: {
    href: "https://github.com/dimensionalOS/dimos/pull/3016",
    label: "PR #3016",
  },
  lastUpdated: "July 18, 2026",
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
        "explanation": "PGO fixes the loop ends — and quietly bends the middle by 1.4 m.",
        "src": "/dimensional/pgo_marker_explainer.png"
      },
      {
        "src": "/dimensional/rerun_pgo_marker_explainer_screenshot.png",
        "title": "The same evidence, live in the team's 3D viewer (rerun)",
        "explanation": "The same evidence in 3D: dots are data, globes are conclusions."
      },
      {
        "src": "/dimensional/revisit_medians_hardened.png",
        "title": "PGO cuts long-gap revisit disagreement to 0.17–0.42 m (raw odometry: 0.37–8.8 m)",
        "explanation": "Hardened for expert review: 5 fresh PGO runs per recording (dots), bootstrap bands over visits, detection floors, caveats printed on the figure — in-sample for the tune, consistency not absolute accuracy. Replaces the 3-run version, which failed an independent-rerun acceptance test (village3's PGO median swings 0.22–0.47 m across fresh runs)."
      },
      {
        "title": "One referee tag, three systems — the cross-village benchmark chart",
        "explanation": "Same tag, three systems — the spread is the error.",
        "src": "/dimensional/benchmark_odom_pgo_module.png"
      },
      {
        "title": "hk_village1 — each system's own placements of the referee tag",
        "explanation": "Sub-gate solves scatter to 1.8 m — attempts the live robot would refuse.",
        "src": "/dimensional/benchmark_hk_village1.png"
      },
      {
        "title": "hk_village3 — the reference recording, per-system tag placements",
        "explanation": "At loop returns PGO wins: 0.93 → 0.28 m.",
        "src": "/dimensional/benchmark_hk_village3.png"
      },
      {
        "title": "hk_village5 — the neutral case for PGO at loop returns",
        "explanation": "A tie: little drift to fix, nothing made worse.",
        "src": "/dimensional/benchmark_hk_village5.png"
      },
      {
        "title": "hk_village6 — PGO's clearest win on the referee tag",
        "explanation": "PGO's best village: 4.5× tighter at loop returns.",
        "src": "/dimensional/benchmark_hk_village6.png"
      },
      {
        "src": "/dimensional/pgo_marker_explainer_village6.png",
        "title": "village6 spatially — what a 0.67 → 0.31 m improvement looks like",
        "explanation": "PGO's best village, drawn: three visits merge into one cluster; the final pass still sits apart. Better, not solved."
      },
      {
        "title": "Why villages 2 and 4 are excluded — one id, several physical tags",
        "explanation": "One printed id, three physical tags — stats invalid, recordings excluded.",
        "src": "/dimensional/benchmark_excluded_duplicate_ids.png"
      },
      {
        "title": "192 m outdoor walk, no tags — PGO's own correction profile",
        "explanation": "1.45 m of drift over a 192 m walk, visible to the eye.",
        "src": "/dimensional/hk_building_all_around_pgo_profile.png"
      },
      {
        "title": "go2_hongkong_office — drift profile of the indoor eval map",
        "explanation": "Indoors too: meter-scale drift corrected over 186 m.",
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
        "explanation": "Wrong answers score up to 0.995 — the gate cannot see them.",
        "src": "/dimensional/confidence_fitness_vs_error.png"
      },
      {
        "title": "Risk vs coverage — what each accept threshold actually buys",
        "explanation": "0.45 filters nothing; safety costs 40 % of the correct answers.",
        "src": "/dimensional/confidence_risk_coverage.png"
      },
      {
        "title": "Is fitness a probability? — reliability of the published score",
        "explanation": "Fitness is not a probability.",
        "src": "/dimensional/confidence_reliability.png"
      },
      {
        "title": "One fitness gate, five environments — does the threshold transfer?",
        "explanation": "No single threshold transfers between environments.",
        "src": "/dimensional/confidence_cross_recording.png"
      },
      {
        "title": "Live rehearsal finding: 1° of tag-orientation error moves the fix 0.55 m at a 31 m lever",
        "explanation": "The full live chain ran in replay (camera → tag → fix → judge) and the judge correctly rejected every scattered single-tag fix — geometry, not detection quality, was the cause (ρ +0.97). Deployment rule that falls out: survey tags near the map origin, prefer two tags in view.",
        "src": "/dimensional/live_fix_quality_village3.png"
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


// Read-this-first definitions shown above the evidence log.
export const glossary: { term: string; def: string }[] = [
  {
    "term": "fitness",
    "def": "the score published with every answer: the fraction of currently-seen wall points that land on map walls at the claimed pose (0–1). A ratio, not a probability."
  },
  {
    "term": "the 0.45 gate",
    "def": "the code's accept threshold — answers scoring below are discarded, at/above are used by the robot."
  },
  {
    "term": "the 50k gate",
    "def": "the live robot won't attempt a solve until its accumulated submap has ≥ 50,000 points."
  },
  {
    "term": "correct / wrong",
    "def": "within 1 m and 15° of truth = correct (right basin — refinable to cm); outside = wrong (a teleport). A catastrophe detector, not a precision bar."
  },
  {
    "term": "PGO silver truth",
    "def": "the reference pose from offline map optimization; “silver” because it is the best available and measurably imperfect (≈6 cm–decimeter softness)."
  },
  {
    "term": "referee tag",
    "def": "one tag per space used only for grading — never in the marker map, never a fix."
  },
  {
    "term": "ransac",
    "def": "today's stack: the global geometric search proposes candidate poses; the judge picks by fitness."
  },
  {
    "term": "ransac+fiducial",
    "def": "same search, plus marker-derived candidates competing in the same pool — no special treatment."
  },
  {
    "term": "fiducial+judge",
    "def": "search switched off: marker candidates only, still verified by the judge. The ‘markers visible → search stands down’ mode."
  },
  {
    "term": "coverage / risk",
    "def": "for a given gate: the share of answers accepted / the share of accepted answers that were actually wrong."
  }
];

// Provenance ledger — the 25 most instructive knobs from the full 108-constant
// audit (complete ledger: trial/harness/PROVENANCE.md in the trial repo).
// tested = swept/validated against data · partial = reasoned or validated in hindsight · arbitrary = no recorded basis
export type Knob = { knob: string; value: string; status: "tested" | "partial" | "arbitrary"; note: string };
export const provenance: Knob[] = [
  { knob: "final full-cloud ICP max_iteration (stage 3)", value: "50", status: "arbitrary", note: "autoresearch removed this stage ('remove final tight ICP refine; median 0.029→0.028'); HEAD re-adds it in a squash-merge, no rationale" },
  { knob: "FiducialPrior age_tau_s (+ trial mirror AGE_TAU_S)", value: "30.0 s", status: "arbitrary", note: "self-admitted 'engineering guesses, not tuned values' at both definition sites; the n=120 benchmark ran with them but never varied them" },
  { knob: "FiducialPrior conf_max (+ trial mirror CONF_MAX)", value: "0.9", status: "arbitrary", note: "ordinal rationale only (fiducial > ransac 0.5 > last-pose 0.3); judge-inert — proven by test_fiducial_prior_never_bypasses_judge" },
  { knob: "RELOC_INTERVAL", value: "2.0 s", status: "arbitrary", note: "born in the poc as an inter-attempt sleep; live solves take 3–8 s so 2.0 rarely binds — an observation recorded nowhere as the reason" },
  { knob: "loop_submap_half_range (classic)", value: "10", status: "arbitrary", note: "recorded evidence favors larger: 10→20 'better ICP context', 20→40 'restoring best'; classic retains 10 with no recorded reason" },
  { knob: "min_loop_detect_duration (classic)", value: "5.0 s", status: "arbitrary", note: "autoresearch measured 3.0 s better on the eval (33.66→31.26 m); classic keeps 5.0 with no recorded reason" },
  { knob: "ISAM2 relinearizeThreshold / relinearizeSkip", value: "0.01 / 1", status: "arbitrary", note: "departs from GTSAM defaults (0.1/10) with no recorded reason; the only ISAM2 setting ever tested is the Dogleg switch" },
  { knob: "eval marker_max_speed gate", value: "0.5 m/s", status: "arbitrary", note: "set in 'detector tuned' — verified EMPTY commit body; held fixed by autoresearch mandate; trial harness re-uses it verbatim" },
  { knob: "eval marker_smoothing", value: "7.5 s", status: "arbitrary", note: "same empty-body 'detector tuned' origin; the trial referee deliberately runs 0.0 'so every sighting counts' — divergence, not validation" },
  { knob: "min_tags (tag-corroboration gate default)", value: "1", status: "arbitrary", note: "exposure driven by a review comment, but 1 = no corroboration required before publishing a world→map fix — simply the permissive minimum" },
  { knob: "ArUco detector parameters", value: "cv2.aruco.DetectorParameters() — all OpenCV library defaults", status: "arbitrary", note: "no in-repo tuning of any detector parameter, ever; PR #2107's 4 m real-life detection is usage evidence, not parameter selection" },
  { knob: "GRAVITY_TILT_MAX_DEG", value: "10.0 deg", status: "partial", note: "downgraded from tested on re-read: the single probe tied success and median (5-micron delta), decided on a third-order tiebreak" },
  { knob: "MIN_LOCAL_POINTS", value: "50_000", status: "partial", note: "value trajectory unexplained; trial stratification: all 27 accepted-failures sub-50k — MIN_LOCAL_POINTS, not fitness, protects the robot" },
  { knob: "Config.fitness_threshold", value: "0.45", status: "partial", note: "born 0.6 → 0.45 via empty-body 'tune default parameters'; docs still say 0.6; measured: risk@0.45 = 22.5% accepted-wrong on hk_village3" },
  { knob: "odom_rot_var", value: "1e-6 rad^2", status: "partial", note: "comment says 'tuned for a Go2-class ground robot' — the word 'tuned' has no run behind it; rotation variance never swept anywhere" },
  { knob: "marker_length_m (Go2 deployment + eval marker_size default)", value: "0.1 m", status: "partial", note: "physical print kit is 100 mm (runbook + verified on recording); pinned by test; no committed caliper measurement of the printed tags" },
  { knob: "eval DEFAULT_DATASETS", value: "hk_village1..6 (range(1,7))", status: "partial", note: "trial found v2/v4 each contain MULTIPLE physical tags sharing id 10 — TOTAL_SPREAD aggregates two poisoned datasets, incl. the v2 win" },
  { knob: "PGOConfig shipped defaults (as a set)", value: "loop/odom variances, radii, thresholds", status: "partial", note: "eval.py's docstring: the tuning eval minimizes TOTAL_SPREAD on hk_village1..6 — the same recordings Fig 3 measures, so its 0.17–0.42 m is in-sample for the tune; lesh (Jul 18): a still-more-aggressive hk-specific tune existed, judged unnecessary" },
  { knob: "max_reprojection_error_px (per-tag PnP accept gate)", value: "3.0 px", status: "partial", note: "introduced with no rationale; system validated AT 3.0 (SIMULATED ATE 1.75→0.33 m) — the value itself never swept against alternatives" },
  { knob: "SCALE_PLAN", value: "[(0.2, 8), (0.3, 8), (0.8, 1)] (voxel_size m, RANSAC runs)", status: "tested", note: "fully bracketed in results.tsv: adding 0.2 m gained a frame, dropping 0.3/0.8 or shifting them lost frames; restart counts swept per scale" },
  { knob: "RANSAC_ITERS", value: "500_000", status: "tested", note: "bracketed both directions: 250k/400k lost frames, 2M blew the time budget; per-scale and combo variants all discarded" },
  { knob: "rerank top-K", value: "10", status: "tested", note: "fully bracketed: 5→10 rescued frame 7 (1.31 m → 0.36 m); 15 identical but +64 s; back to 7 or 5 lost frames" },
  { knob: "min_loop_detect_duration (auto)", value: "3.0 s", status: "tested", note: "commit '5 → 3 (more loops in disturbed areas)'; conclusions.md win table verified: 33.66 → 31.26 m" },
  { knob: "chain-loosening drift gate", value: "1.3 m", status: "tested", note: "nine alternatives tried (1.0–3.0 + multi-tier) — '1.3 m is sharply optimal'; pairs with the tested 8e-4 drifted-chain variance" },
  { knob: "rescan time_thresh_override", value: "13.0 s", status: "tested", note: "commit chain 20→10→15→12→13; conclusions.md verified: '13s (not 12 or 14)', final spread 22.29" },
  { knob: "ambiguity_ratio_min (IPPE mirror-pose gate)", value: "2.0 (ratio; 1.0 disables)", status: "tested", note: "smallest tested value keeping ≥95% of accepted poses correct; kills 100% of flips at 110° HFOV, ATE 0.33→0.26 m — ALL SIMULATED" },
  { knob: "FULL LEDGER", value: "trial/harness/PROVENANCE.md", status: "partial", note: "complete audit: arbitrary 49 · partial 30 · tested 29 — 108 unique constants" },
];

// Notes — Aaryan's running scratch notes, editable on the page.
export const notes: string[] = [];
