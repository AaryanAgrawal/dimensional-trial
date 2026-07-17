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
    "Extends reliable relocalization into the regime the stack currently skips (small submaps): 61.4% → 92.9% on those sections; large-submap sections were already 100%",
    "Markers visible → the global search can stand down: 95.0% at 0.4 s median vs 9.7 s — ~25× cheaper",
    "Adversarially verified framing: marker map is survey-grade by construction (shares the map's own PGO frame); cross-recording decorrelation is the named next test",
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
