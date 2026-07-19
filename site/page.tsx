import { Fragment, type ReactNode } from "react";
import OpenQuestions from "./OpenQuestions";
import {
  architectureSection,
  baselines,
  evidence,
  glossary,
  notes,
  nextSection,
  provenance,
  referenceSection,
  stackMap,
  openQuestions,
  metrics,
  oneRunFigure,
  phase1,
  phase2,
  phase4,
  runFlow,
  runFlowNote,
  trial,
  type Status,
} from "@/data/dimensional";

// Unlisted page — not in nav, not in sitemap.ts. Shared by direct link only.
export const metadata = {
  title: "Relocalization Benchmark + Visual Relocalization Primitive — Aaryan Agrawal",
  description: "Relocalization benchmark and visual relocalization primitive — Dimensional FDE trial.",
  robots: { index: false, follow: false },
};

const dotClass: Record<Status, string> = {
  active: "bg-amber-500",
  done: "bg-emerald-500",
  pending: "border border-faint",
};

const flowTint: Record<string, string> = {
  neutral: "border-line bg-tile",
  amber: "border-amber-700/30 bg-amber-500/10",
  green: "border-emerald-700/30 bg-emerald-600/10",
};

function Flowchart() {
  return (
    <>
      <div className="mt-2 flex flex-col items-stretch gap-3 sm:flex-row sm:items-start">
        {runFlow.map((step, i) => (
          <Fragment key={step.label}>
            {i > 0 ? (
              <span
                className="self-center text-sm leading-none text-faint sm:mt-3"
                aria-hidden
              >
                <span className="sm:hidden">↓</span>
                <span className="hidden sm:inline">→</span>
              </span>
            ) : null}
            <div className="flex flex-1 flex-col items-center gap-2">
              <div
                className={`w-full rounded-md border px-2 py-2 text-center text-xs leading-snug text-soft ${flowTint[step.tint ?? "neutral"]}`}
              >
                {step.label}
              </div>
              {step.image ? (
                <img
                  src={step.image.src}
                  alt={step.image.alt}
                  className="h-14 w-14 rounded-md border border-line object-cover sm:h-16 sm:w-16"
                />
              ) : null}
            </div>
          </Fragment>
        ))}
      </div>
      <p className="mt-2 text-center text-[11px] text-faint">{runFlowNote}</p>
    </>
  );
}

// Big story heading — the title IS the takeaway.
function PhaseHeading({ status, children }: { status?: Status; children: ReactNode }) {
  return (
    <h2 className="flex items-center gap-3 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
      {status ? (
        <span
          className={`h-2.5 w-2.5 shrink-0 rounded-full ${dotClass[status]}`}
          aria-hidden
        />
      ) : null}
      {children}
    </h2>
  );
}

// Sub-heading — phase cards and reference blocks nested under a story title.
function SubHeading({ status, children }: { status?: Status; children: ReactNode }) {
  return (
    <h3 className="flex items-center gap-3 text-lg font-semibold tracking-tight text-ink sm:text-xl">
      {status ? (
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${dotClass[status]}`}
          aria-hidden
        />
      ) : null}
      {children}
    </h3>
  );
}

function InCode({ children }: { children: ReactNode }) {
  return <p className="mt-4 text-xs text-faint">In code: {children}</p>;
}

function PhasePlan({ items, label = "Plan" }: { items: string[]; label?: string }) {
  return (
    <div className="mt-3 rounded-md border border-line bg-tile px-3 py-2.5">
      <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-faint">
        {label}
      </p>
      <ul className="mt-1.5 space-y-1 text-xs">
        {items.map((p) => (
          <li key={p} className="flex gap-2">
            <span className="text-faint" aria-hidden>
              –
            </span>
            <span className="text-soft">{p}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Figure numbering runs continuously across all evidence sections, in page order.
const evidenceNumbered = (() => {
  let n = 0;
  return evidence.map((sec) => ({
    ...sec,
    figures: sec.figures.map((f) => ({ ...f, n: ++n })),
  }));
})();
const [problemSection, ...laterSections] = evidenceNumbered;

type NumberedSection = (typeof evidenceNumbered)[number];

// One story section of the evidence log: takeaway title, intro, optional plan
// card, numbered figures with one-line takeaway captions, optional live-exhibit
// callout and closing paragraph.
function EvidenceBlock({ sec }: { sec: NumberedSection }) {
  return (
    <section className="mt-12 border-t border-line pt-6">
      <PhaseHeading>{sec.heading}</PhaseHeading>
      <p className="mt-3 text-sm text-soft">{sec.intro}</p>
      {sec.plan ? <PhasePlan items={sec.plan.items} label={sec.plan.label} /> : null}
      {sec.figures.map((f) => (
        <figure key={f.src} className="mt-6">
          <img src={f.src} alt={f.title} className="w-full rounded-md bg-tile" />
          <figcaption className="mt-2 text-xs text-soft">
            <span className="font-medium text-ink">Fig {f.n} · {f.title}.</span> {f.explanation}
          </figcaption>
        </figure>
      ))}
      {sec.callout ? (
        <div className="mt-6 rounded-lg border border-amber-700/30 bg-amber-500/10 px-4 py-3">
          <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-faint">
            {sec.callout.label}
          </p>
          <p className="mt-1.5 text-sm text-soft">{sec.callout.text}</p>
        </div>
      ) : null}
      {sec.close ? <p className="mt-6 text-sm text-soft">{sec.close}</p> : null}
    </section>
  );
}

export default function DimensionalPage() {
  return (
    <article className="mx-auto max-w-2xl">
      {/* Header */}
      <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-faint">
        {trial.kicker}
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
        {trial.title}
      </h1>

      <div className="mt-4 rounded-lg border border-line bg-tile px-4 py-3">
        <p className="text-base font-semibold text-ink sm:text-lg">{trial.objective}</p>
      </div>

      <ul className="mt-4 space-y-1 text-sm">
        {trial.deliverables.map((d, i) => (
          <li key={d} className="flex gap-2">
            <span className="text-faint" aria-hidden>
              {i + 1}.
            </span>
            <span className="text-soft">{d}</span>
          </li>
        ))}
      </ul>

      <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
        <a
          href={trial.pr.href}
          target="_blank"
          rel="noreferrer"
          className="rounded-full border border-line px-2.5 py-1 text-soft hover:text-ink"
        >
          {trial.pr.label} ↗
        </a>
      </div>

      {/* §1 — the problem: the score can't see wrong answers */}
      <EvidenceBlock sec={problemSection} />

      {/* §2 — the architectural fix: priors + one judge (Phase 1 + Phase 2) */}
      <section className="mt-12 border-t border-line pt-6">
        <PhaseHeading>{architectureSection.heading}</PhaseHeading>
        <p className="mt-3 text-sm text-soft">{architectureSection.intro}</p>

        {/* Phase 1 card */}
        <div className="mt-8">
          <SubHeading status={phase1.status}>{phase1.title}</SubHeading>
          <p className="mt-3 text-sm font-medium text-ink">{phase1.objective}</p>
          <PhasePlan items={phase1.plan} />

          <Flowchart />

          <div className="mt-5 flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-xs text-soft">
            {baselines.map((b, i) => (
              <Fragment key={b.label}>
                {i > 0 ? <span className="text-faint">·</span> : null}
                <span>
                  <span className="font-medium text-ink">{b.label}</span>{" "}
                  <span className="text-faint">({b.detail})</span>
                </span>
              </Fragment>
            ))}
          </div>

          <figure className="mt-5">
            <img
              src={oneRunFigure.src}
              alt={oneRunFigure.alt}
              className="w-full rounded-md bg-tile"
            />
            <figcaption className="mt-1 text-center text-xs text-faint">
              {oneRunFigure.caption}
            </figcaption>
          </figure>

          <h4 className="mt-5 text-xs font-medium uppercase tracking-[0.08em] text-faint">
            Metrics tracked
          </h4>
          <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-soft">
            {metrics.map((m, i) => (
              <Fragment key={m.name}>
                {i > 0 ? <span className="text-faint">·</span> : null}
                <span>
                  <span className="font-medium text-ink">{m.name}</span>
                  {m.note ? <span className="text-faint"> ({m.note})</span> : null}
                </span>
              </Fragment>
            ))}
          </div>

          <InCode>
            <span className="font-mono text-soft">{phase1.code}</span>
          </InCode>
        </div>

        {/* Phase 2 card */}
        <div className="mt-10">
          <SubHeading status={phase2.status}>{phase2.title}</SubHeading>
          <p className="mt-3 text-sm font-medium text-ink">{phase2.objective}</p>
          <PhasePlan items={phase2.plan} />
          <ul className="mt-3 space-y-1 text-sm">
            {phase2.lines.map((l) => (
              <li key={l} className="flex gap-2">
                <span className="text-faint" aria-hidden>
                  ·
                </span>
                <span className="text-soft">{l}</span>
              </li>
            ))}
          </ul>
          <figure className="mt-4">
            <img
              src={phase2.demo.src}
              alt={phase2.demo.alt}
              className="w-full rounded-md bg-tile"
            />
            <figcaption className="mt-1 text-xs text-faint">
              {phase2.demo.caption}
            </figcaption>
          </figure>

          <InCode>
            <span className="font-mono text-soft">{phase2.codeLead}</span> · class{" "}
            <span className="font-mono text-soft">{phase2.codeClass}</span> ·{" "}
            <a
              href={trial.pr.href}
              target="_blank"
              rel="noreferrer"
              className="underline decoration-line underline-offset-2 hover:text-ink"
            >
              {trial.pr.label} ↗
            </a>
          </InCode>
        </div>
      </section>

      {/* §3–§7 — grading method, offline verdict, live rehearsal, robot day, truth qualification */}
      {laterSections.map((sec) => (
        <EvidenceBlock key={sec.heading} sec={sec} />
      ))}

      {/* §8 — what's next (Phase 4 card + open questions) */}
      <section className="mt-12 border-t border-line pt-6">
        <PhaseHeading>{nextSection.heading}</PhaseHeading>
        <p className="mt-3 text-sm text-soft">{nextSection.intro}</p>

        <div className="mt-8">
          <SubHeading status={phase4.status}>{phase4.title}</SubHeading>
          <p className="mt-3 text-sm font-medium text-ink">{phase4.objective}</p>
          <PhasePlan items={phase4.plan} />

          <div className="mt-4 flex flex-col items-stretch gap-1.5 sm:flex-row sm:items-center sm:justify-center">
            <div className="rounded-md border border-line bg-tile px-3 py-2 text-center text-xs text-soft sm:flex-none">
              {phase4.nodes[0]}
            </div>
            <span className="self-center text-sm leading-none text-faint" aria-hidden>
              +
            </span>
            <div className="rounded-md border border-line bg-tile px-3 py-2 text-center text-xs text-soft sm:flex-none">
              {phase4.nodes[1]}
            </div>
            <span className="self-center text-sm leading-none text-faint" aria-hidden>
              <span className="sm:hidden">↓</span>
              <span className="hidden sm:inline">→</span>
            </span>
            <div className="rounded-md border border-emerald-700/30 bg-emerald-600/10 px-3 py-2 text-center text-xs text-soft sm:flex-none">
              {phase4.nodes[2]}
            </div>
          </div>

          <p className="mt-3 text-center text-xs text-faint">{phase4.line}</p>

          <pre className="mt-5 overflow-x-auto rounded-md bg-tile p-4 font-mono text-[12.5px] leading-relaxed text-ink">
            {phase4.tree}
          </pre>
        </div>

        {/* Open questions — editable: add / edit / delete, saved in-browser */}
        <div className="mt-10">
          <SubHeading>Open questions</SubHeading>
          <OpenQuestions seeds={openQuestions} />
        </div>
      </section>

      {/* §9 — reference tier: everything load-bearing but off the story's spine */}
      <section className="mt-12 border-t border-line pt-6">
        <PhaseHeading>{referenceSection.heading}</PhaseHeading>
        <p className="mt-2 text-xs text-faint">{referenceSection.lead}</p>

        {/* Glossary */}
        <div className="mt-8">
          <SubHeading>Glossary — the terms above, precisely</SubHeading>
          <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
            {glossary.map((g) => (
              <div key={g.term} className="text-sm">
                <dt className="font-medium text-ink">{g.term}</dt>
                <dd className="text-xs text-soft">{g.def}</dd>
              </div>
            ))}
          </dl>
        </div>

        {/* The stack map */}
        <div className="mt-10">
          <SubHeading>The stack — existed · added · open</SubHeading>
          <div className="mt-2 flex gap-3 text-[11px] text-faint">
            <span><span className="mr-1 inline-block h-2 w-2 rounded-full border border-line bg-tile" />existed</span>
            <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-500" />this trial</span>
            <span><span className="mr-1 inline-block h-2 w-2 rounded-full border border-dashed border-amber-500" />gap (designed)</span>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {stackMap.map((col) => (
              <div key={col.job} className="rounded-lg border border-line bg-tile p-3">
                <p className="text-sm font-semibold text-ink">{col.job}</p>
                <p className="text-[11px] text-faint">{col.ask}</p>
                {[["live", col.live], ["dev-time", col.dev]].map(([label, pills]) => (
                  <div key={label as string} className="mt-2">
                    <p className="text-[10px] uppercase tracking-[0.08em] text-faint">{label as string}</p>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {(pills as { t: string; s: string }[]).map((p) => (
                        <span key={p.t} className={
                          p.s === "added" ? "rounded-full border border-emerald-700/40 bg-emerald-600/10 px-2 py-0.5 text-[11px] text-soft"
                          : p.s === "gap" ? "rounded-full border border-dashed border-amber-600/50 px-2 py-0.5 text-[11px] text-faint"
                          : "rounded-full border border-line px-2 py-0.5 text-[11px] text-soft"
                        }>{p.t}</span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* Provenance ledger */}
        <div className="mt-10">
          <SubHeading>Tested vs arbitrary — the provenance ledger</SubHeading>
          <p className="mt-2 text-xs text-faint">every constant in the stack, and whether its value has backing</p>
          <div className="mt-3 space-y-1.5">
            {provenance.map((k) => (
              <div key={k.knob} className="flex items-start gap-2 text-xs">
                <span className={
                  k.status === "tested" ? "mt-0.5 shrink-0 rounded-full border border-emerald-700/40 bg-emerald-600/10 px-1.5 text-[10px] text-soft"
                  : k.status === "partial" ? "mt-0.5 shrink-0 rounded-full border border-line px-1.5 text-[10px] text-soft"
                  : "mt-0.5 shrink-0 rounded-full border border-dashed border-amber-600/50 px-1.5 text-[10px] text-faint"
                }>{k.status}</span>
                <span className="font-medium text-ink">{k.knob}</span>
                <span className="font-mono text-soft">{k.value}</span>
                <span className="text-faint">— {k.note}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Notes */}
        <div className="mt-10">
          <SubHeading>Notes</SubHeading>
          <OpenQuestions seeds={notes} storeKey="dimensional-notes" exportName="notes" bullet="·" placeholder="type a note…" />
        </div>
      </section>

      {/* Footer */}
      <p className="mt-8 border-t border-line pt-3 text-[11px] text-faint">
        unlisted · last updated {trial.lastUpdated}
      </p>
    </article>
  );
}
