"""Declarative response shapes accepted from domain-onboarding models."""

from __future__ import annotations

from .structured_response import FieldRule, ResponseContract


OBJECT_LIST = dict(
    kind="array",
    item_kind="object",
    singleton_to_array=True,
    drop_invalid_items=True,
)


DOMAIN_PLAN_CONTRACT = ResponseContract(
    name="domain_plan",
    fields={
        "normalized_domain": FieldRule("string", required=True, aliases=("domain",)),
        "translated_domain": FieldRule("string"),
        "expanded_terms": FieldRule("array", item_kind="string", drop_invalid_items=True),
        "perspectives": FieldRule(required=True, **OBJECT_LIST),
        "search_queries": FieldRule("array", required=True, item_kind="string", drop_invalid_items=True),
        "paper_queries": FieldRule(**OBJECT_LIST),
        "expected_subdirections": FieldRule("array", required=True, item_kind="string", drop_invalid_items=True),
        "subdirection_plans": FieldRule(**OBJECT_LIST),
    },
    wrappers=("plan", "research_plan", "result", "data", "output"),
)

STAGE_PLANNING_CONTRACT = ResponseContract(
    name="development_stage_planning",
    fields={
        "development_stage_plans": FieldRule(
            required=True,
            aliases=("stage_plans", "stages", "phases"),
            **OBJECT_LIST,
        )
    },
    wrappers=("stage_planning", "plan", "result", "data", "output"),
)

DEVELOPMENT_FOUNDATION_CONTRACT = ResponseContract(
    name="development_foundation",
    fields={
        "domain": FieldRule("string", aliases=("normalized_domain",)),
        "text": FieldRule("string", aliases=("summary", "overview")),
        "prerequisites": FieldRule(
            required=True,
            aliases=("foundations", "prerequisite_knowledge"),
            **OBJECT_LIST,
        ),
    },
    wrappers=("development_foundation", "foundation", "development", "result", "data", "output"),
)

DEVELOPMENT_STAGE_CONTRACT = ResponseContract(
    name="development_stage",
    fields={
        "development_stage": FieldRule(
            "object",
            required=True,
            aliases=("stage", "development_stages"),
            singleton_array_to_value=True,
        ),
        "paper_guidance": FieldRule(**OBJECT_LIST),
        "evidence_claims": FieldRule(**OBJECT_LIST),
    },
    wrappers=("result", "data", "output"),
    direct_field="development_stage",
    direct_required_keys=("stage_id", "name", "summary"),
)

DEVELOPMENT_SECTION_CONTRACT = ResponseContract(
    name="development_section",
    fields={
        "domain": FieldRule("string"),
        "text": FieldRule("string", aliases=("summary", "overview")),
        "prerequisites": FieldRule(required=True, **OBJECT_LIST),
        "development_stages": FieldRule(
            required=True,
            aliases=("stages", "phases", "history", "evolution"),
            **OBJECT_LIST,
        ),
        "paper_guidance": FieldRule(**OBJECT_LIST),
        "evidence_claims": FieldRule(**OBJECT_LIST),
    },
    wrappers=("development", "result", "data", "output"),
)

LANDSCAPE_SECTION_CONTRACT = ResponseContract(
    name="landscape_section",
    fields={
        "current_landscape": FieldRule(
            "object",
            required=True,
            aliases=("landscape",),
        ),
        "evidence_claims": FieldRule(**OBJECT_LIST),
    },
    wrappers=("landscape", "result", "data", "output"),
    direct_field="current_landscape",
    direct_required_keys=("problems", "subdirections"),
)

LEARNING_PATH_SECTION_CONTRACT = ResponseContract(
    name="learning_path_section",
    fields={
        "learning_path": FieldRule(
            required=True,
            aliases=("steps", "learning_steps", "path"),
            **OBJECT_LIST,
        ),
        "evidence_claims": FieldRule(**OBJECT_LIST),
    },
    wrappers=("learning_path_section", "result", "data", "output"),
)

REPAIR_PATCH_CONTRACT = ResponseContract(
    name="repair_patch",
    fields={"repairs": FieldRule(required=True, **OBJECT_LIST)},
    wrappers=("repair", "patch", "result", "data", "output"),
)

FULL_ONBOARDING_CONTRACT = ResponseContract(
    name="full_onboarding",
    fields={
        "domain": FieldRule("string", required=True),
        "text": FieldRule("string"),
        "prerequisites": FieldRule(required=True, **OBJECT_LIST),
        "development_stages": FieldRule(
            required=True,
            aliases=("stages", "phases"),
            **OBJECT_LIST,
        ),
        "current_landscape": FieldRule("object", required=True, aliases=("landscape",)),
        "learning_path": FieldRule(
            required=True,
            aliases=("steps", "learning_steps", "path"),
            **OBJECT_LIST,
        ),
        "paper_guidance": FieldRule(**OBJECT_LIST),
        "evidence_claims": FieldRule(**OBJECT_LIST),
    },
    wrappers=("onboarding", "result", "data", "output"),
)


SECTION_CONTRACTS = {
    "development": DEVELOPMENT_SECTION_CONTRACT,
    "landscape": LANDSCAPE_SECTION_CONTRACT,
    "learning_path": LEARNING_PATH_SECTION_CONTRACT,
}
