// /dimensional (unlisted trial hub) — public, shared on Dimensional's Discord.
// All page data lives here so an update = edit this file + `vercel --prod`.
// PUBLIC-SAFETY: additive framing only. No critique of dimos, no internal/
// Discord/roadmap references, no unverified % claims. This is what Aaryan is
// building, framed positively and generous to the existing system.

export const trial = {
  kicker: "Dimensional FDE Trial · Aaryan Agrawal · July 15 – July 24",
  title: "Relocalization Benchmark + Visual Relocalization Primitive",
  objective: "Main Objective: more accurate and better-degrading relocalization",
  deliverables: [
    "Relocalization benchmark",
    "Visual relocalization primitive",
    "Fused relocalization module (if time permits)",
  ],
  pr: {
    href: "https://github.com/dimensionalOS/dimos/pull/2808",
    label: "PR #2808",
  },
  lastUpdated: "July 15, 2026",
};

export type Status = "active" | "done" | "pending";

export type FlowStep = {
  label: string;
  tint?: "amber" | "green";
  image: { src: string; alt: string };
};

// The run flowchart — how one benchmark run is measured. Each step carries
// a small rendered tile of that step.
export const runFlow: FlowStep[] = [
  {
    label: "Face the start/end tag",
    image: { src: "/dimensional/flow/face-tag.png", alt: "Robot facing the start/end fiducial tag" },
  },
  {
    label: "START — reference frozen",
    image: { src: "/dimensional/flow/start-frozen.png", alt: "Start reference pose frozen" },
  },
  {
    label: "Drive the route",
    tint: "amber",
    image: { src: "/dimensional/flow/drive-route.png", alt: "Robot driving the route" },
  },
  {
    label: "Return — box turns green",
    tint: "green",
    image: { src: "/dimensional/flow/return-green.png", alt: "Return detected, indicator turns green" },
  },
  {
    label: "STOP — error recorded",
    image: { src: "/dimensional/flow/stop-recorded.png", alt: "Stop — return error recorded" },
  },
];
export const runFlowNote =
  "the start/end tag measures truth; each method's estimate is compared to it";

// The three baselines, presented as a neutral row.
export const baselines = [
  { label: "odom", detail: "dead-reckoning" },
  { label: "lidar", detail: "RelocalizationModule" },
  { label: "visual", detail: "this work" },
];

// Two-panel figure: top-down trajectory + error-over-time for one run.
export const oneRunFigure = {
  src: "/dimensional/one-run.png",
  alt: "Two-panel figure — top-down trajectory and error over time for one benchmark run, simulated",
  caption: "What one benchmark run produces (simulated)",
};

export const metrics: { name: string; note?: string }[] = [
  { name: "Return error", note: "position + heading, vs. the start/end tag — primary" },
  { name: "Max checkpoint error" },
  { name: "Recovery time", note: "kidnap-recovery trials" },
  { name: "Bounded vs. unbounded error growth" },
];

// Phase 1 — the benchmark.
export const phase1 = {
  status: "active" as Status,
  title: "Phase 1: Benchmark",
  objective: "Objective: measure relocalization accuracy on real hardware.",
  code: "dimos/mapping/benchmark/",
};

// Phase 2 — the visual relocalization primitive (PR #2808).
export const phase2 = {
  status: "done" as Status,
  title: "Phase 2: Visual relocalization primitive",
  objective: "Objective: add a camera-based relocalization source.",
  lines: [
    "VisualRelocalizationModule — PR #2808",
    "One board, self-surveyed by the robot",
    "Publishes a correction alongside the existing lidar path",
  ],
  demo: {
    src: "/dimensional/demo.gif",
    alt: "Simulated validation run of the visual relocalization module",
    caption: "Simulated validation run",
  },
  codeLead: "dimos/perception/fiducial",
  codeClass: "VisualRelocalizationModule",
};

// Phase 3 — re-run the benchmark with visual added.
export const phase3 = {
  status: "pending" as Status,
  title: "Phase 3: Re-run the benchmark",
  objective: "Objective: compare odom · lidar · visual on the same runs.",
};

// Phase 4 — fusion and runtime degradation.
export const phase4 = {
  status: "pending" as Status,
  title: "Phase 4: Fusion and runtime degradation",
  objective:
    "Objective: when more than one source is active, fuse them for a single, better-degrading correction.",
  nodes: ["lidar", "visual", "fused correction"],
  line: "sources plug in; an arbiter owns the correction; a healthy source always carries the pose.",
  tree: `dimos/mapping/relocalization/
  base.py    # source contract
  lidar.py   # existing source
  visual.py  # this work
  fusion.py  # arbiter — owns the correction`,
};
