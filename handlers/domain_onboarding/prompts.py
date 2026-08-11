"""领域入门的 Skill 驱动分模块 Prompt。"""

from __future__ import annotations

from functools import lru_cache

from skills.registry import create_skill_registry

DOMAIN_ONBOARDING_SKILL_ID = "domain.onboarding_guide"


@lru_cache(maxsize=1)
def domain_onboarding_skill_instructions() -> str:
    """从统一 Skill Registry 加载领域入门工作流。"""

    return create_skill_registry().get_instructions(DOMAIN_ONBOARDING_SKILL_ID)


def planning_system_prompt() -> str:
    return (
        "You are the research-planning component of the following domain-onboarding skill. "
        "Return one JSON object only. First isolate the research domain from the user's phrasing, "
        "then plan multiple complementary retrieval perspectives for an ordinary research beginner. "
        "Never personalize from background, goals, preferences or time budget. "
        "Schema: normalized_domain:string, translated_domain:string, expanded_terms:[string], "
        "perspectives:[{path_id,name,description,questions:[string],search_queries:[string]}], "
        "search_queries:[string], expected_subdirections:[string]. If the input is Chinese, translate "
        "the domain into its standard English research term and add useful aliases, abbreviations and "
        "related technical terms to expanded_terms. Return exactly 3 perspectives with no more than 2 "
        "questions each, exactly 1 path-specific English search query each, 3-5 expanded_terms and "
        "exactly 3 expected_subdirections. Give each perspective a stable path_id. Search queries must "
        "use precise English technical terms and cover surveys, "
        "foundational work, methods, evaluation and recent progress. expected_subdirections must be "
        "real research branches, not paper-role labels.\n\n"
        + domain_onboarding_skill_instructions()
    )


def development_stage_planning_prompt(language: str) -> str:
    return (
        "You plan the chronological research stages for a domain-onboarding pipeline. "
        "Return one compact JSON object only with exactly 3 development_stage_plans. "
        "These stages explain real changes in research problems and methods, not a learner schedule. "
        "Each item must contain stage_id, sequence, name, period, focus, "
        "transition_from_previous and exactly 1 precise English search query. Keep focus and transition "
        "to one short sentence each. Period must be a real "
        "calendar range or named historical era. Search queries should target papers from that "
        "specific stage and combine the standard English domain name with representative methods. "
        "Do not include paper IDs or invent citations. The first transition may be empty; later "
        f"transitions must be explicit. Explanatory fields should use {language}."
    )


def development_foundation_prompt(language: str) -> str:
    return (
        "Generate only the foundation block for a domain-onboarding result. Return one JSON object "
        "with only domain, text and exactly 3 prerequisites. Text must be a 2-3 sentence introduction "
        "of at least 60 Chinese characters (or 40 words in another language). Each "
        "prerequisite must contain name, why_needed, exactly 2 key_points and related_paper_ids. "
        "Each key point uses {name, explanation, why_it_matters, related_paper_ids}. Use only supplied "
        "paper IDs. Limit text, why_needed, explanation and why_it_matters to one short sentence each. "
        "Do not generate paper_guidance, evidence_claims, development_stages, landscape or learning_path. "
        "The JSON must end immediately after the prerequisites array with ]}; any additional key is invalid. "
        f"Write beginner-facing prose in {language}."
    )


def development_stage_content_prompt(language: str) -> str:
    return (
        "Generate exactly one researched historical development stage. Return one JSON object with "
        "development_stage, paper_guidance and evidence_claims. Copy stage_id, sequence, name, period "
        "and transition_from_previous from stage_research_plan. The stage must contain summary, "
        "motivation, related_paper_ids, exactly 2 core_concepts, exactly 2 main_techniques, no more "
        "than 3 open_problems, exactly "
        "one `breakthroughs` array item and prerequisite_ids. The breakthrough object must contain "
        "name, description and supporting_paper_ids. Concepts and techniques need concise explanations, "
        "why_it_matters, mechanism for techniques, and related_paper_ids. Use only the supplied "
        "stage_papers and never cite papers from another stage. Include concise reading guidance for each "
        "used paper and no more than 2 evidence_claims. Each evidence claim must be "
        "{claim, supporting_paper_ids, support_type}; use abstract_explicit only when the supplied abstract "
        "directly supports the wording, otherwise use metadata_inference or background_synthesis. "
        "Limit every explanatory field to one or two "
        f"sentences. Write beginner-facing prose in {language}.\n\n"
        + domain_onboarding_skill_instructions()
    )


def section_system_prompt(section: str, language: str) -> str:
    common = (
        "You generate one grounded section of a domain-onboarding result. Return one JSON object only. "
        f"Write beginner-facing explanations in {language}; preserve English paper titles and established "
        "technical terms. Use only supplied paper IDs and stage/problem/subdirection IDs. Do not infer "
        "personal background, preferences, goals or schedules. Every evidence_claim must be "
        "{claim, supporting_paper_ids, support_type}; use abstract_explicit only for wording directly "
        "supported by a supplied abstract, metadata_inference only for title/year inferences, and "
        "background_synthesis otherwise.\n\n"
    )
    instructions = {
        "development": (
            "Return domain, text, exactly 3 prerequisites, exactly 3 chronological development_stages, "
            "paper_guidance and up to 4 evidence_claims. Each prerequisite key point and stage core concept "
            "must be {name, explanation, why_it_matters, related_paper_ids}. Each main technique must also "
            "include mechanism. Explain each concept or technique in one or two concrete sentences. Each "
            "stage must follow development_stage_plans exactly in sequence, identity and period, explain "
            "motivation and transition, and use only that stage plan's selected_paper_ids. Include grounded "
            "paper IDs and exactly one breakthrough. paper_guidance must explain each core/recommended "
            "paper's contribution and concrete reading focus."
        ),
        "landscape": (
            "Return current_landscape and up to 4 evidence_claims. current_landscape must contain exactly "
            "3 non-empty problem_details and exactly 3 non-empty subdirection_details; its problems and "
            "subdirections arrays must mirror those detail names. Each problem detail must include "
            "problem_id, name, description, related_paper_ids and related_stage_ids. Each subdirection "
            "detail must include description, "
            "why_it_matters, typical_tasks, prerequisites, common_techniques, datasets_and_benchmarks, "
            "evaluation_metrics, starter_project, research_workflow, research_questions, related_paper_ids, "
            "related_stage_ids, emerged_in_stage_id and addresses_problem_ids. common_techniques use the "
            "structured technique shape with explanation, mechanism and paper IDs. Make the branch usable "
            "as a research starting guide, not a taxonomy label. Relations must be supported by prose or "
            "shared paper evidence. For each subdirection use exactly 3 typical_tasks, at most 3 prerequisites, "
            "exactly 2 common_techniques, at most 3 datasets/benchmarks, at most 3 metrics, exactly 4 research "
            "workflow steps and exactly 3 research questions. Keep each explanatory field to 1-2 sentences."
        ),
        "learning_path": (
            "Return exactly five ordered learning_path steps and up to 3 evidence_claims. Follow: 基础准备, "
            "核心概念, 代表方法与论文, 工具数据集与基线实验, 前沿问题与研究切入. Every step needs a "
            "goal, topics, task-appropriate paper IDs, activities, deliverables, completion criteria, expected "
            "outcome and milestone. The experiment step also needs a reproducibility checklist and evaluation "
            "metrics. estimated_hours may be general guidance but do not create week ranges."
        ),
    }[section]
    return common + instructions + "\n\nSkill contract:\n" + domain_onboarding_skill_instructions()


def full_generation_system_prompt(language: str) -> str:
    return (
        "Generate a complete grounded domain-onboarding JSON object in "
        f"{language}. Use one standard route for an ordinary research beginner and never personalize from "
        "background, goals, preferences or time budget. Use only supplied paper_id values and never alter "
        "paper metadata. Required top-level fields: domain, text, prerequisites, development_stages, "
        "current_landscape, learning_path, paper_guidance, evidence_claims. Follow every module requirement "
        "in the skill contract below. Concepts and techniques must be structured explanatory objects with "
        "paper mappings. Subdirections must include actionable tasks, prerequisites, techniques, datasets, "
        "metrics, a starter project, workflow and research questions. Development periods are calendar years, "
        "not learner weeks. Use stable IDs and grounded cross-module relations. Return JSON only.\n\n"
        + domain_onboarding_skill_instructions()
    )
