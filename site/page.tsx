import { Fragment, type ReactNode } from "react";
import {
  baselines,
  metrics,
  oneRunFigure,
  phase1,
  phase2,
  phase3,
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
              <img
                src={step.image.src}
                alt={step.image.alt}
                className="h-14 w-14 rounded-md border border-line object-cover sm:h-16 sm:w-16"
              />
            </div>
          </Fragment>
        ))}
      </div>
      <p className="mt-2 text-center text-[11px] text-faint">{runFlowNote}</p>
    </>
  );
}

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

function InCode({ children }: { children: ReactNode }) {
  return <p className="mt-4 text-xs text-faint">In code: {children}</p>;
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

      {/* Phase 1 — the benchmark */}
      <section className="mt-12 border-t border-line pt-6">
        <PhaseHeading status={phase1.status}>{phase1.title}</PhaseHeading>
        <p className="mt-3 text-sm font-medium text-ink">{phase1.objective}</p>

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

        <h3 className="mt-5 text-xs font-medium uppercase tracking-[0.08em] text-faint">
          Metrics tracked
        </h3>
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
      </section>

      {/* Phase 2 — the visual relocalization primitive */}
      <section className="mt-12 border-t border-line pt-6">
        <PhaseHeading status={phase2.status}>{phase2.title}</PhaseHeading>
        <p className="mt-3 text-sm font-medium text-ink">{phase2.objective}</p>
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
      </section>

      {/* Phase 3 — re-run the benchmark */}
      <section className="mt-12 border-t border-line pt-6">
        <PhaseHeading status={phase3.status}>{phase3.title}</PhaseHeading>
        <p className="mt-3 text-sm font-medium text-ink">{phase3.objective}</p>
      </section>

      {/* Phase 4 — fusion and runtime degradation */}
      <section className="mt-12 border-t border-line pt-6">
        <PhaseHeading status={phase4.status}>{phase4.title}</PhaseHeading>
        <p className="mt-3 text-sm font-medium text-ink">{phase4.objective}</p>

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
      </section>

      {/* Footer */}
      <p className="mt-8 border-t border-line pt-3 text-[11px] text-faint">
        unlisted · last updated {trial.lastUpdated}
      </p>
    </article>
  );
}
