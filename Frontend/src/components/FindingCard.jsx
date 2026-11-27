import { useState } from "react";
import { CodeTag, ConfidenceBadge, SupportChip } from "./ui.jsx";

const PATTERN = { consistent: "Consistent", occasional: "Occasional" };
const SKILL_STATE = { missing: "Skill missing", misapplied: "Skill misapplied" };

/**
 * One growth area the AI found, read-only: the standard, how much support the student needs,
 * how sure the AI is (a word, never a percentage), and the paragraph-level evidence.
 * Answering findings (agree / disagree / dismiss) is left out of this release.
 */
export default function FindingCard({ finding: f }) {
  const [showStandard, setShowStandard] = useState(false);

  return (
    <article className="card finding2">
      <div className="finding-top">
        <CodeTag title={f.standard_description}>{f.standard_code}</CodeTag>
        <span className="small muted">
          {f.strand} · {f.concept}
        </span>
        <span className="finding-badges">
          <SupportChip supportLevel={f.support_level} label={f.support_level_label} />
          <ConfidenceBadge label={f.confidence_label} />
        </span>
      </div>

      <p className="finding-summary">{f.summary}</p>

      {(f.pattern || f.skill_state) && (
        <p className="tag-row">
          {f.pattern && <span className="tag">{PATTERN[f.pattern] || f.pattern}</span>}
          {f.skill_state && <span className="tag">{SKILL_STATE[f.skill_state] || f.skill_state}</span>}
        </p>
      )}

      {f.evidence?.length > 0 && (
        <div className="evidence">
          <span className="small strong muted">Evidence</span>
          {f.evidence.map((e, i) => (
            <div key={i} className="evidence-row">
              <span className="para-tag">¶ {e.paragraph}</span>
              <span>{e.reason}</span>
            </div>
          ))}
        </div>
      )}

      <button
        type="button"
        className="btn-link-inline small"
        aria-expanded={showStandard}
        onClick={() => setShowStandard((s) => !s)}
      >
        {showStandard ? "Hide the standard" : `Show standard ${f.standard_code}`}
      </button>
      {showStandard && (
        <p className="standard">
          <strong>{f.standard_code}:</strong> {f.standard_description}
        </p>
      )}
    </article>
  );
}