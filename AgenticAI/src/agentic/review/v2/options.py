"""Per-run options for the v2 pipeline, resolved against Settings and the teacher/admin settings."""
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, Field

from agentic.config import Settings
from agentic.llm.base import check_reasoning
from agentic.settings.base import load_defs, validate
from agentic.tuning import SupervisorSpec, check_mode_prompt, load_profile


@dataclass(frozen=True)
class ResolvedOptions:
    """Concrete values: nothing is None."""
    pipeline: str
    profile: str
    prompts: dict[str, str]  # reviewer id -> "<name>.v<N>"
    root_cause: bool
    verify: bool
    settings: dict = field(default_factory=dict)  # effective teacher/admin settings
    reasoning: dict[str, str] = field(default_factory=dict)  # reviewer id -> default|low|medium|high
    supervisor: SupervisorSpec | None = None  # the profile's, with this run's switches applied


class ReviewOptions(BaseModel):
    """What a caller may override for one run; None / empty means "use the default"."""
    pipeline: str | None = None
    profile: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)  # reviewer id -> "v2"
    reasoning: dict[str, str] = Field(default_factory=dict)  # reviewer id -> default|low|medium|high
    root_cause: bool | None = None
    verify: bool | None = None
    settings_override: dict = Field(default_factory=dict)
    planner: bool | None = None  # supervised profile: switch a step off (or on) for this run
    critic: bool | None = None
    arbiter: bool | None = None

    def resolve(self, s: Settings, defaults: dict) -> ResolvedOptions:
        profile = self.profile or s.bands_profile
        loaded = load_profile(s.config_dir, profile)
        reviewers = loaded.reviewers
        switches = {k: v for k, v in (("planner", self.planner), ("critic", self.critic),
                                      ("arbiter", self.arbiter)) if v is not None}
        if switches and loaded.supervisor is None:
            raise ValueError(f"profile {profile!r} has no supervisor: --no-planner, --no-critic and --no-arbiter "
                             "need the supervised profile")
        supervisor = replace(loaded.supervisor, **switches) if loaded.supervisor else None
        prompts = {r.id: r.prompt for r in reviewers}
        for reviewer, version in self.prompt_versions.items():
            if reviewer not in prompts:
                raise ValueError(f"prompt_versions: no reviewer {reviewer!r} in profile {profile!r}")
            prompts[reviewer] = f"{prompts[reviewer].rsplit('.', 1)[0]}.{version}"
        for r in reviewers:
            check_mode_prompt(r.id, r.mode, prompts[r.id])
        for reviewer, level in self.reasoning.items():
            if reviewer not in prompts:
                raise ValueError(f"reasoning: no reviewer {reviewer!r} in profile {profile!r}")
            check_reasoning(level, f"reasoning for {reviewer!r}")
        # precedence: this run's option, the reviewer's bands.toml value, LLM_REASONING, default
        levels = {r.id: self.reasoning.get(r.id) or r.reasoning or s.llm_reasoning for r in reviewers}
        defs = load_defs(s.config_dir)
        for key, value in self.settings_override.items():  # same rules as an admin override
            if key not in defs:
                raise ValueError(f"settings_override: unknown setting {key!r}")
            validate(defs[key], value)
        effective = {**defaults, **self.settings_override}
        return ResolvedOptions(
            pipeline=self.pipeline or s.review_pipeline, profile=profile, prompts=prompts, reasoning=levels,
            root_cause=self.root_cause if self.root_cause is not None else bool(effective.get("root_cause_enabled")),
            verify=self.verify if self.verify is not None else s.verify,
            settings=effective, supervisor=supervisor,
        )
