"""Streamlit demo harness for the essay-review MVP (not the product UI).

Tabs: Review (run one essay through the MVP review), Compare reports (saved evaluations; no model calls),
Review one (one v2 review with chosen options, saved to local/reviews), Compare runs (two saved runs) and
Simulation (the semester simulator's clock, pipeline and classroom grid).
"""
import tomllib

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
from agentic.review.v2.options import ReviewOptions
from agentic.coursedata import CourseDir
from agentic_lab import review_runs, sim_view, summary_view
from agentic_lab.sim import findings_query
from agentic_lab.sim import wiring as sim_wiring
from agentic_lab.sim.schedule import schedule
from agentic_lab.sim.settings import load_sim_settings
from agentic_lab.sim.simulator import AssignmentStatus
from agentic_lab.sim.state import load_state
from agentic_lab.sim.trigger import TriggerError
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
        mvp = s.model_copy(update={"review_pipeline": "mvp"})  # the strategy picker is an MVP tool
        reviewer = wiring.build_reviewer(mvp, strategy=STRATEGIES[strategy_label], strands=strands,
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


def _profiles(s: Settings) -> list[str]:
    with open(s.config_dir / "bands.toml", "rb") as f:
        return list(tomllib.load(f).get("profiles", {}))


def _tri(col, label: str, key: str) -> bool | None:
    pick = col.selectbox(label, ["default", "on", "off"], key=key)
    return {"default": None, "on": True, "off": False}[pick]


def _table(rows: list[dict], empty: str) -> None:
    if rows:
        st.dataframe(rows, hide_index=True)
    else:
        st.write(empty)


def render_run(s: Settings, saved: review_runs.SavedRun, key: str) -> None:
    """One saved run as tables. Codes, counts and short model notes only; the trace sits in a collapsed expander."""
    result = saved.result
    expected = None
    if st.checkbox("Show the answer key's expected labels", key=f"{key}-expected"):
        expected = review_runs.expected_for(s, saved.course, saved.assignment, saved.student)
        if expected is None:
            st.info("This course has no answer key for this assignment.")
    v = review_runs.view(result, s, profile=saved.options.profile, expected=expected)
    t = v["totals"]
    usd = "unknown" if t["usd"] is None else f"{t['usd']:.4f}"
    st.caption(f"Pipeline {result.pipeline} · submission type {result.submission_type} · verification "
               f"{t['verification']} · {t['calls']} calls · {t['input_tokens']} in / {t['output_tokens']} out "
               f"tokens · USD {usd}"
               + (f" (no price for {', '.join(t['unpriced_models'])})" if t["unpriced_models"] else ""))
    st.subheader("Verdicts by reviewer")
    _table(v["verdicts"], "No verdicts.")
    st.subheader("Growth areas")
    _table(v["growth_areas"], "No growth areas.")
    if expected is not None:
        st.write("Planted but not reported: " + (", ".join(v["missed"]) or "none"))
    st.subheader("Root-cause links")
    _table(v["links"], "No links.")
    st.subheader("Strengths")
    _table(v["strengths"], "No notable strengths.")
    st.subheader("Reviewers: prompt, tokens, time")
    _table(v["usage"], "No usage recorded.")
    with st.expander("raw reviewer reply (may quote the student)"):
        for reviewer, entry in result.trace.get("reviewers", {}).items():
            st.markdown(f"**{reviewer}**")
            st.json(entry.get("reply", {}), expanded=False)


def render_review_one(s: Settings) -> None:
    versions = review_runs.course_versions(s)
    if not versions:
        st.info(f"No course folders in {s.data_dir}/courses.")
        return
    c1, c2, c3 = st.columns(3)
    version = c1.selectbox("Course", versions, key="one-course")
    try:
        assignments = review_runs.assignment_choices(s, version)
    except (FileNotFoundError, ValueError) as err:
        st.error(str(err))  # content-free: names files/fields only
        return
    titles = dict(assignments)
    assignment = c2.selectbox("Assignment", list(titles), key="one-assignment",
                              format_func=lambda a: f"{a}: {titles[a]}")
    students = review_runs.student_choices(s, version, assignment) if assignment else []
    if not students:
        st.info("No submissions for this course and assignment yet.")
        return
    captions = dict(students)
    student = c3.selectbox("Student", list(captions), format_func=captions.get, key="one-student")
    o1, o2, o3, o4, o5 = st.columns(5)
    profile = o1.selectbox("Profile", [None, *_profiles(s)], key="one-profile",
                           format_func=lambda p: "default" if p is None else p)
    root_cause = _tri(o2, "Root cause", "one-root")
    verify = _tri(o3, "Verify", "one-verify")
    pairs = o4.text_input("Prompt versions (reviewer=v2, comma separated)", key="one-prompts")
    levels = o5.text_input("Reasoning (reviewer=low, comma separated)", key="one-reasoning")
    if st.button("Run review", key="one-run"):
        try:
            options = ReviewOptions(
                profile=profile, root_cause=root_cause, verify=verify,
                prompt_versions=dict(p.strip().split("=", 1) for p in pairs.split(",") if p.strip()),
                reasoning=dict(p.strip().split("=", 1) for p in levels.split(",") if p.strip()))
            with st.spinner("Reviewing..."):
                result = review_runs.run_review(s, version, assignment, student, options)
            path = review_runs.save_run(result, options, review_runs.runs_dir_for(s))
        except Exception as e:  # noqa: BLE001 - message may quote submission text; class name only
            st.error(f"Review failed: {type(e).__name__}")
            return
        st.session_state["one-last"] = str(path)
    last = st.session_state.get("one-last")
    if last:
        st.caption(f"Saved to {last}")
        render_run(s, review_runs.load_run(last), "one")


def render_compare_runs(s: Settings) -> None:
    runs = review_runs.list_runs(review_runs.runs_dir_for(s))
    if len(runs) < 2:
        st.info(f"Need two saved runs in {review_runs.runs_dir_for(s)}; use the Review one tab.")
        return
    names = [p.name for p in runs]
    a_col, b_col = st.columns(2)
    a_name = a_col.selectbox("Run A", names, index=1, key="cmp-a")
    b_name = b_col.selectbox("Run B (newest first)", names, index=0, key="cmp-b")
    a, b = (review_runs.load_run(runs[names.index(n)]) for n in (a_name, b_name))
    d = review_runs.diff_runs(a.result, a.options, b.result, b.options)
    st.caption(f"A: {a.course} {a.assignment} {a.student[:8]} · B: {b.course} {b.assignment} {b.student[:8]}")
    st.subheader("Options that differ")
    _table([{"option": k, "A": x, "B": y} for k, (x, y) in d["options"].items()], "Same options.")
    st.subheader("Tokens and calls")
    st.dataframe([{"run": "A", "tokens": d["tokens"]["a"], "calls": d["calls"]["a"]},
                  {"run": "B", "tokens": d["tokens"]["b"], "calls": d["calls"]["b"]}], hide_index=True)
    st.subheader("Verdicts and support levels that differ")
    _table(d["verdicts"], "No differences.")
    left, right = st.columns(2)
    with left:
        st.subheader("Growth areas only in A")
        st.write(", ".join(d["only_a"]) or "None.")
        st.subheader("Links only in A")
        _table(d["links_only_a"], "None.")
    with right:
        st.subheader("Growth areas only in B")
        st.write(", ".join(d["only_b"]) or "None.")
        st.subheader("Links only in B")
        _table(d["links_only_b"], "None.")


SIM_VERSIONS = ["2.00-control", "2.00-intervention"]
LEVEL_SHADE = {"approaching": "#fff3c4", "developing": "#ffd08a", "beginning": "#f5a3a3"}  # light to dark


def _cell_text(cell, planted: int | None) -> str:
    """Count and strengths, e.g. "2 ★1", plus the planted level when the lab-only overlay is on."""
    parts = []
    if cell is not None:
        if cell.count:
            parts.append(str(cell.count))
        if cell.strengths:
            parts.append(f"★{cell.strengths}")
    text = " ".join(parts)
    return f"{text or '–'} | gt {planted}" if planted else text


def _fallback_status(data_dir, state) -> list[AssignmentStatus]:
    """Status rows from the saved state alone (released and sent), for when run states are not loaded."""
    rows = []
    for version in SIM_VERSIONS:
        for item in schedule(data_dir, version):
            n = sum(1 for e in state.sent if (e.course_id, e.assignment_id) == (item.course_id, item.lms_id))
            rows.append(AssignmentStatus(version, item.file_id, item.week, item.week <= state.week, n))
    return rows


def _session_simulator():
    """The Simulator, built once per session so its HTTP clients are reused across reruns."""
    if "sim" not in st.session_state:
        st.session_state["sim"] = sim_wiring.build_simulator(SIM_VERSIONS)
    return st.session_state["sim"]


def render_simulation(s: Settings) -> None:
    """The simulator's clock, the per-assignment pipeline, and the classroom grid of findings by skill area.
    Codes, counts, levels and dates only; the grid reads no student text. Course folders come from
    SIM_DATA_DIR (the lab's v2 data by default), not DATA_DIR."""
    sim_settings = load_sim_settings()
    sim_dir, data = sim_settings.sim_dir, sim_settings.sim_data_dir
    try:
        for v in SIM_VERSIONS:
            CourseDir(data, v).course()
    except (FileNotFoundError, LookupError, ValueError):
        st.info(f"No {' or '.join(SIM_VERSIONS)} course folders in SIM_DATA_DIR.")
        return
    try:
        sim = _session_simulator()
    except Exception as err:  # noqa: BLE001 - class name only
        st.error(type(err).__name__)
        return
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    advance = c2.button("Advance one week", key="sim-advance")
    reset = c3.button("Reset", key="sim-reset")
    refresh = c4.button("Refresh run states", key="sim-refresh")
    if advance or reset:
        try:
            with st.spinner("Working..."):
                sim.advance(1) if advance else sim.reset()
        except TriggerError as err:
            st.error(str(err))
        except Exception as err:  # noqa: BLE001 - never a traceback or a message that could quote text
            st.error(type(err).__name__)
    state = load_state(sim_dir)
    c1.metric("Current week", state.week)
    as_of = state.week
    if state.week >= 1:
        as_of = st.slider("As of week", 0, state.week, state.week, key="sim-asof")

    st.subheader("Pipeline")
    # Run states cost one GET /runs/{id} per sent event, so they are fetched only on Refresh or after
    # Advance/Reset, and reused while the number of sent events is unchanged.
    if advance or reset or refresh:
        try:
            with st.spinner("Fetching run states..."):
                st.session_state["sim-status"] = (len(state.sent), sim.status())
        except Exception:  # noqa: BLE001 - service down or unreadable; fall back to the saved state
            st.session_state.pop("sim-status", None)
            st.warning("The agentic service is not reachable; run states are unknown")
    cached = st.session_state.get("sim-status")
    if cached is not None and cached[0] == len(state.sent):
        status, run_states = cached[1], True
    else:
        status, run_states = _fallback_status(data, state), False
        st.caption("Run states not loaded; press Refresh run states.")
    details = sim_view.assignment_details(data, SIM_VERSIONS)
    _table(sim_view.pipeline_rows(status, as_of, details, run_states=run_states), "Nothing is due yet.")

    marks = [m for m in sim_view.intervention_marks(data, SIM_VERSIONS[1]) if m["week"] <= as_of]
    st.subheader("Interventions")
    _table([{"week": m["week"], "id": m["id"], "kind": m["kind"],
             "students": m["students"] if m["students"] == "all" else len(m["students"])} for m in marks],
           "None yet.")

    st.subheader("Skill areas")
    side = st.toggle("Side by side (control | intervention)", value=False, key="sim-side")
    show_gt = st.toggle("Ground truth (lab only)", value=False, key="sim-gt")
    version = st.radio("Classroom", SIM_VERSIONS, horizontal=True, key="sim-class") if not side else None
    try:
        conn = wiring.connect(s)
    except Exception:  # noqa: BLE001 - nothing useful in the message
        st.warning("Postgres is not reachable; showing the pipeline only")
        return
    try:
        through = sim_view.week_end(CourseDir(data, SIM_VERSIONS[0]).course().start_date, as_of)
        cells = {v: findings_query.findings_by_area(conn, f"classroom_{v}", through=through)
                 for v in SIM_VERSIONS}
    except Exception as err:  # noqa: BLE001 - class name only
        st.error(type(err).__name__)
        return
    finally:
        conn.close()
    try:
        planted = ({v: sim_view.ground_truth(data, v, as_of, s.standards_file) for v in SIM_VERSIONS}
                   if show_gt else {})
        rosters = {v: [r.student_hash for r in CourseDir(data, v).roster()] for v in SIM_VERSIONS}
        pairs = sim_view.roster_pairs(data, *SIM_VERSIONS) if side else []
    except Exception as err:  # noqa: BLE001 - class name only: a message may carry a file path
        st.error(type(err).__name__)
        return
    in_iv = {h for m in marks for h in (rosters[SIM_VERSIONS[1]] if m["students"] == "all" else m["students"])}
    if side:
        rows, areas, values = sim_view.grid(cells[SIM_VERSIONS[0]] + cells[SIM_VERSIONS[1]], pairs)
        index = {f"p{i + 1:02d}": p for i, p in enumerate(pairs)}
        extra = {a for ver in SIM_VERSIONS for (_, a) in planted.get(ver, {})}  # planted areas without a finding

        def text_of(r, a, v):
            return " | ".join(_cell_text(cell, planted.get(ver, {}).get((h, a)))
                              for ver, h, cell in zip(SIM_VERSIONS, index[r], v or (None, None)))

        def level_of(v):
            return findings_query.highest_level(c.level for c in v if c) if v else None

        marked = {r for r, (_, i) in index.items() if i in in_iv}
    else:
        rows, areas, values = sim_view.grid(cells[version])
        full = {h[:8]: h for h in rosters[version]} | {c.student_hash[:8]: c.student_hash for c in cells[version]}
        extra = {a for (_, a) in planted.get(version, {})}
        rows = sorted(set(rows) | {h[:8] for (h, _) in planted.get(version, {}) if h[:8] in full})

        def text_of(r, a, v):
            return _cell_text(v, planted.get(version, {}).get((full[r], a)))

        def level_of(v):
            return v.level if v else None

        marked = {r for r in rows if version == SIM_VERSIONS[1] and full[r] in in_iv}
    areas = sorted(set(areas) | extra, key=findings_query.area_sort_key)
    if not rows:
        st.write("No reviewed findings yet.")
        return
    import pandas as pd

    labels = [f"{r} ◆" if r in marked else r for r in rows]
    text = pd.DataFrame([[text_of(r, a, values.get((r, a))) for a in areas] for r in rows],
                        index=labels, columns=areas)
    shade = pd.DataFrame([[LEVEL_SHADE.get(level_of(values.get((r, a))), "") for a in areas] for r in rows],
                         index=labels, columns=areas)
    st.dataframe(text.style.apply(lambda _: shade.map(lambda c: f"background-color: {c}" if c else ""), axis=None))
    st.caption("Count of findings and ★ strengths per skill area; shading is the highest support level "
               "(light approaching, medium developing, dark beginning)"
               + ("; ◆ marks students in an intervention" if marked else "")
               + ("; gt is the planted level (lab only)" if show_gt else ""))


def _summary_source(s: Settings, data, version: str):
    """The Summary for the chosen source, or None after showing why there is none."""
    source = st.radio("Source", ["Postgres (latest analysis)", "Answer key (no reviews needed)"],
                      horizontal=True, key="sum-source")
    if source.startswith("Answer key"):
        try:
            with st.spinner("Analyzing the answer key..."):
                return summary_view.answer_key_summary(data, version)
        except Exception as err:  # noqa: BLE001 - class name only
            st.error(type(err).__name__)
            return None
    try:
        conn = wiring.connect(s)
    except Exception:  # noqa: BLE001 - nothing useful in the message
        st.info("Postgres is not reachable; choose the answer-key source.")
        return None
    try:
        found = summary_view.postgres_summary(conn, CourseDir(data, version).course().id)
    except Exception as err:  # noqa: BLE001 - class name only
        st.info(f"No analysis could be read ({type(err).__name__}).")
        return None
    finally:
        conn.close()
    if found is None:
        st.info("No analysis saved for this classroom yet.")
    return found


def render_class_summary(s: Settings) -> None:
    """Status counts, common gaps, reteach groups (root-cause chains), pair suggestions, a concepts x weeks
    heatmap and the twin divergence chart. Ids, codes, levels and counts only. Course folders come from
    SIM_DATA_DIR, like the Simulation tab."""
    import altair as alt
    import pandas as pd

    data = load_sim_settings().sim_data_dir
    try:
        for v in SIM_VERSIONS:
            CourseDir(data, v).course()
    except (FileNotFoundError, LookupError, ValueError):
        st.info(f"No {' or '.join(SIM_VERSIONS)} course folders in SIM_DATA_DIR.")
        return
    version = st.radio("Classroom", SIM_VERSIONS, horizontal=True, key="sum-class")
    found = _summary_source(s, data, version)
    if found is None:
        return
    st.subheader("Status")
    _table(summary_view.status_rows(found.statuses), "No statuses.")
    st.subheader("Common gaps")
    _table(summary_view.gap_rows(found.gaps), "No concept needs attention.")
    st.subheader("Reteach groups")
    _table(summary_view.group_rows(found.groups), "No groups.")
    st.subheader("Peer pair suggestions")
    _table(summary_view.pair_rows(found.pairs), "No pairs.")
    if not found.pairs and st.session_state.get("sum-source", "").startswith("Answer key"):
        st.caption("The answer key records no strengths, so no student can reach Secure (Secure needs a "
                   "strength); pairs appear with real reviews.")

    st.subheader("Concepts by week")
    grouped = st.toggle("Group by strand and band", value=False, key="sum-group")
    rows, starts, values = summary_view.heatmap(found.weeks, group=grouped)
    cells = pd.DataFrame([{"concept": r, "week": str(d), "mean level": values[(r, d)]}
                          for r in rows for d in starts])
    if cells.empty:
        st.write("No weekly data.")
    else:
        st.altair_chart(alt.Chart(cells).mark_rect().encode(
            x=alt.X("week:O"), y=alt.Y("concept:O", sort=rows),
            color=alt.Color("mean level:Q", scale=alt.Scale(domain=[0, 3], scheme="yelloworangered")),
            tooltip=["concept", "week", "mean level"]), width="stretch")
        st.caption("Mean current level of the students with a level (0 meets .. 3 beginning); blank means "
                   "no student has a level yet.")

    st.subheader("Twin divergence (planted levels from the answer key are lab only)")
    ids = summary_view.intervention_ids(data, SIM_VERSIONS[1])
    if not ids:
        st.write("No interventions.")
        return
    chosen = st.selectbox("Intervention", ids, key="sum-iv")
    reader = None
    try:
        if not st.session_state.get("sum-source", "").startswith("Answer key"):
            try:
                reader = wiring.build_analytics_reader(s)
            except Exception:  # noqa: BLE001 - fall back to the planted-only chart
                reader = None
        d = summary_view.divergence(data, chosen, reader=reader)
    except Exception as err:  # noqa: BLE001 - class name only
        st.error(type(err).__name__)
        return
    finally:
        close = getattr(reader, "close", None)
        if close:
            close()
    line = alt.Chart(pd.DataFrame(summary_view.divergence_rows(d))).mark_line(point=True).encode(
        x=alt.X("week:Q"), y=alt.Y("level:Q", scale=alt.Scale(domain=[0, 3])), color="classroom:N",
        strokeDash=alt.StrokeDash("source:N", legend=alt.Legend(title="series")))
    marks = alt.Chart(pd.DataFrame(d["marks"])).mark_rule(strokeDash=[4, 4]).encode(
        x="week:Q", tooltip=["id", "week"])
    st.altair_chart(line + marks, width="stretch")
    st.caption("Weekly mean evidence level over the intervention's skill-family concepts; dashed rules are "
               "intervention weeks. "
               + ("Solid lines are what SASS measured from saved reviews; the dashed-line series is the "
                  "answer key's planted levels (lab only, not a measurement)."
                  if d["planted"] else
                  "These are the answer key's planted levels (lab only), not a SASS measurement."))


st.set_page_config(page_title="SASS essay review demo", layout="wide")
st.title("SASS essay review demo")
settings = lab_settings()
review_tab, compare_tab, one_tab, runs_tab, sim_tab, summary_tab = st.tabs(
    ["Review", "Compare reports", "Review one", "Compare runs", "Simulation", "Class summary"])
with review_tab:
    render_review(settings)
with compare_tab:
    render_compare(settings)
with one_tab:
    render_review_one(settings)
with runs_tab:
    render_compare_runs(settings)
with sim_tab:
    render_simulation(settings)
with summary_tab:
    render_class_summary(settings)
st.caption("MVP scope: grade-level writing standards, plus reading (RL/RI) when selected; "
           "grammar and below-grade gaps are not checked.")
