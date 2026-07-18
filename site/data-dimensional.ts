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
        "explanation": "PGO fixes the loop ends — and quietly bends the middle by 1.4 m.",
        "src": "/dimensional/pgo_marker_explainer.png"
      },
      {
        "src": "/dimensional/rerun_pgo_marker_explainer_screenshot.png",
        "title": "The same evidence, live in the team's 3D viewer (rerun)",
        "explanation": "The same evidence in 3D: dots are data, globes are conclusions."
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
