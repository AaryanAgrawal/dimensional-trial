"use client";

import { useEffect, useState } from "react";

export default function OpenQuestions({
  seeds,
  storeKey = "dimensional-open-questions",
  exportName = "openQuestions",
  bullet = "?",
  placeholder = "type your question…",
}: {
  seeds: string[];
  storeKey?: string;
  exportName?: string;
  bullet?: string;
  placeholder?: string;
}) {
  const STORE_KEY = storeKey;
  const [questions, setQuestions] = useState<string[]>(seeds);
  const [loaded, setLoaded] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) setQuestions(JSON.parse(raw));
    } catch {
      /* corrupted store -> keep seeds */
    }
    setLoaded(true);
  }, []);

  const save = (next: string[]) => {
    setQuestions(next);
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(next));
    } catch {
      /* storage full/blocked -> state still updates for this session */
    }
  };

  const copyAsCode = async () => {
    const kept = questions.filter((q) => q.trim().length > 0);
    const code =
      `export const ${exportName}: string[] = [\n` +
      kept.map((q) => `  ${JSON.stringify(q.trim())},`).join("\n") +
      "\n];";
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (!loaded) return null;

  return (
    <div className="mt-3">
      <div className="flex flex-wrap gap-2 text-xs">
        <button
          onClick={() => save(["", ...questions])}
          className="rounded-full border border-line px-2.5 py-1 text-soft hover:text-ink"
        >
          + add
        </button>
        <button
          onClick={copyAsCode}
          className="rounded-full border border-line px-2.5 py-1 text-soft hover:text-ink"
        >
          {copied ? "copied ✓" : "copy as code (to publish)"}
        </button>
        <button
          onClick={() => {
            localStorage.removeItem(STORE_KEY);
            setQuestions(seeds);
          }}
          className="rounded-full border border-line px-2.5 py-1 text-faint hover:text-ink"
        >
          reset to published
        </button>
      </div>
      <ul className="mt-3 space-y-2 text-sm">
        {questions.map((q, i) => (
          <li key={i} className="flex items-start gap-2">
            <span className="mt-2 text-faint" aria-hidden>
              {bullet}
            </span>
            <textarea
              value={q}
              placeholder={placeholder}
              rows={Math.max(1, Math.ceil(q.length / 90))}
              onChange={(e) => {
                const next = [...questions];
                next[i] = e.target.value;
                save(next);
              }}
              className="w-full resize-none rounded-md border border-line bg-tile px-2 py-1.5 text-soft outline-none focus:border-faint"
            />
            <button
              onClick={() => save(questions.filter((_, j) => j !== i))}
              aria-label="delete question"
              className="mt-1.5 text-faint hover:text-ink"
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-faint">
        edits save in this browser only — “copy as code” and paste into
        src/data/dimensional.ts (or hand it to the agent) to publish
      </p>
    </div>
  );
}
