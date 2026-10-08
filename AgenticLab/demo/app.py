"""Streamlit demo harness for the essay-review MVP (not the product UI).

Tabs: Review (run one essay through the review) and Compare reports (saved evaluations; no model calls).
"""
import streamlit as st

from agentic import dataset, wiring
from agentic.config import Settings
from agentic_lab.config import lab_settings
from agentic_lab.labels import load_labels
from agentic_lab.demo_view import (
    calibration_rows, compare, comparison_lines, list_eval_reports, load_runs, not_evident_rows,
    student_diff, student_options, summary_rows,
)
from agentic.models import SubmissionRef
from agentic.review.nodes import html_to_paragraphs

STRATEGIES = {"Full list": "full", "RAG": "rag"}


def render_review(s: Settings) -> None:
    try:
        essays = {e.student_hash: e for e in dataset.load_essays(s.data_dir, s.classroom)}
        labels = load_labels(s.data_dir, s.classroom)
        assignment = dataset.load_assignment(s.data_dir, s.classroom)
    except (FileNotFoundError, ValueError) as err:
        st.error(str(err))  # content-free: names files/fields only
        return

    options = [o for o in student_options(labels) if o[0] in essays]
    by_caption = {cap: h for h, cap in options}
    c1, c2, c3 = st.columns([3, 2, 1])
    picked = c1.selectbox("Student", list(by_caption))
    student = by_caption.get(picked)
    strategy_label = c2.radio("Strategy", list(STRATEGIES), horizontal=True)
    run = c3.button("Run review")
    if assignment.prompt:
        with st.expander(f"Assignment: {assignment.title or assignment.id}"):
            st.write(assignment.prompt)
            if assignment.targets:
                st.caption("Targeted standards: " + ", ".join(assignment.targets))
            if assignment.notes:
                st.markdown("**Focus by standard**  \n"
                            + "  \n".join(f"{code}: {note}" for code, note in assignment.notes.items()))
    r1, r2, r3 = st.columns([2, 2, 2])
    strands = None  # targeted assignments fix the scope; strand boxes only apply without targets
    if not assignment.targets:
        review_rl = r1.checkbox("Review RL (literary text)", value="RL" in assignment.strands)
        review_ri = r2.checkbox("Review RI (informational text)", value="RI" in assignment.strands)
        strands = ["W"] + (["RL"] if review_rl else []) + (["RI"] if review_ri else [])
    use_context = r3.checkbox("Use assignment context", value=s.assignment_context)

    if not (run and student):
        return
    row = essays[student]
    ref = SubmissionRef(course_id=row.course_id, assignment_id=row.assignment_id,
                        user_id=row.student_hash, submission_id=row.submission_id,
                        attempt=row.attempt, submitted_at=row.submitted_at)
    steps: list[tuple[str, float]] = []
    try:
        reviewer = wiring.build_reviewer(s, strategy=STRATEGIES[strategy_label], strands=strands,
                                         context=use_context)
        with st.spinner("Reviewing..."):
            for node, seconds in reviewer.stream(ref):
                steps.append((node, seconds))
        result = reviewer.last_result
        body = wiring.build_lms(s, s.classroom).get_submission(
            row.course_id, row.assignment_id, row.student_hash).body
        paragraphs = html_to_paragraphs(body)
    except Exception as e:  # noqa: BLE001 - message may quote essay text; class name only
        st.error(f"Review failed: {type(e).__name__}")
        return

    st.caption(f"Strands reviewed: {', '.join(reviewer.strands)}"
               + (f" · Targets: {', '.join(reviewer.targets)}" if reviewer.targets else "")
               + f" · Assignment context: {'on' if reviewer.assignment_context else 'off'}")
    left, right = st.columns(2)
    with left:
        st.subheader("Essay")
        for i, p in enumerate(paragraphs, start=1):
            st.markdown(f"**¶{i}** {p}")
    with right:
        st.subheader("Findings")
        if not result.findings:
            st.write("No findings.")
        for f in result.findings:
            pc = "n/a" if f.p_correct is None else f"{f.p_correct:.2f}"
            sev = f" · *{f.severity}*" if f.severity else ""
            st.markdown(f"**{f.standard_code}** · {f.concept}{sev}  \n"
                        f"¶ {', '.join(str(n) for n in f.paragraphs)}  \n"
                        f"**P(correct)** {pc}")
            for reason in f.reasons:
                st.markdown(f"- {reason}")
        st.subheader("Possible missed weaknesses")
        status = result.verification.status
        if status == "ok":
            misses = [m for m in result.met_standards if m.possible_miss]
            if not misses:
                st.write("None flagged.")
            for m in misses:
                p = "n/a" if m.p_met is None else f"{m.p_met:.2f}"
                st.markdown(f"- {m.standard_code} · {m.concept} (P(met) {p})")
        elif status == "unavailable":
            st.write("Verification unavailable.")
        else:
            st.write("Verification disabled.")
        st.subheader("Not evident")
        st.write(", ".join(result.not_evident) if result.not_evident
                 else "None: every standard in scope was judged.")

    st.divider()
    st.subheader("Expected vs found (concept level)")
    label = next(r for r in labels if r.student_hash == student)
    cmp = compare({f.standard_code for f in result.findings},
                  {u.standard for u in label.underdeveloped})
    st.text("\n".join(comparison_lines(cmp)))
    st.caption("✓ expected and found · ✗ expected, not found · + found, not expected")
    with st.expander("Steps"):
        for node, seconds in steps:
            st.write(f"{node}: {seconds:.2f}s")
        cands = result.trace.get("candidates")
        if cands:
            st.write("Candidate codes per issue:")
            for issue, codes in cands.items():
                st.write(f"{issue}: {', '.join(codes)}")


def render_compare(s: Settings) -> None:
    reports = list_eval_reports(s.reports_dir)
    if not reports:
        st.info(f"No saved evaluations in {s.reports_dir}. Run `python -m agentic evaluate` first.")
        return
    picked = st.multiselect("Evaluation reports (newest first)", [p.name for p in reports])
    if not picked:
        st.caption("Pick one or more reports. Each strategy run in a report becomes a column.")
        return
    runs, skipped = load_runs([s.reports_dir / name for name in picked])
    for name in skipped:
        st.warning(f"Skipped {name}: not a readable evaluation report.")
    if not runs:
        return
    st.subheader("Summary")
    st.dataframe(summary_rows(runs), hide_index=True)

    st.subheader("Per-student diff")
    cols = list(runs)
    a_col, b_col = st.columns(2)
    a = a_col.selectbox("A", cols, index=0)
    b = b_col.selectbox("B", cols, index=min(1, len(cols) - 1))
    diff = student_diff(runs[a], runs[b])
    st.dataframe(diff, hide_index=True)
    st.caption("Clean = the student meets all standards (expected is empty); any finding there is a false alarm.")

    cal = calibration_rows(runs)
    if cal:
        st.subheader("Finding check calibration")
        st.caption("Fraction of findings actually correct per Jev probability bin "
                   "(well calibrated: the 0.8–1.0 bin is about 0.8–1.0 correct).")
        st.dataframe(cal, hide_index=True)
        st.line_chart(cal, x="Bin")

    ne = not_evident_rows(runs)
    if ne:
        st.subheader("Not evident (count per standard)")
        st.dataframe(ne, hide_index=True)


st.set_page_config(page_title="SASS essay review demo", layout="wide")
st.title("SASS essay review demo")
settings = lab_settings()
review_tab, compare_tab = st.tabs(["Review", "Compare reports"])
with review_tab:
    render_review(settings)
with compare_tab:
    render_compare(settings)
st.caption("MVP scope: grade-level writing standards, plus reading (RL/RI) when selected; "
           "grammar and below-grade gaps are not checked.")
