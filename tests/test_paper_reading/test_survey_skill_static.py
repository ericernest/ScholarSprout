from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
HANDLER = ROOT / "handlers" / "paper_reading" / "handler.py"
APP_JS = ROOT / "gateway" / "static" / "paper-reading" / "app.js"
SKILL = ROOT / "skills" / "builtin" / "reading" / "novice_map_builder" / "SKILL.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SurveySkillStaticTests(unittest.TestCase):
    def test_survey_skill_contains_quality_contract(self):
        text = read(SKILL)
        self.assertIn("<!-- survey_map_skill:start -->", text)
        self.assertIn("planner 选节规范", text)
        self.assertIn("Intro 全局上下文规范", text)
        self.assertIn("综述前置知识卡片规范", text)
        self.assertIn("代表方法强 schema", text)
        self.assertIn("数据集强 schema", text)
        self.assertIn("禁止输出 `Item 1`", text)

    def test_survey_pipeline_uses_planner_and_card_tasks(self):
        text = read(HANDLER)
        self.assertIn("return _build_survey_plan_card_reading_map(", text)
        self.assertIn("def _survey_section_manifest", text)
        self.assertIn("def _plan_survey_cards", text)
        self.assertIn("def _generate_survey_card", text)
        self.assertIn("survey_card_plan", text)
        self.assertIn("survey_card_results", text)
        self.assertIn("survey_card_progress", text)
        self.assertIn("section_text_hash", text)
        self.assertIn("skill_hash", text)
        self.assertIn("planning_sections", text)
        self.assertIn("generating_cards", text)
        self.assertIn("failed_partial", text)

    def test_planner_manifest_exposes_quality_signals(self):
        text = read(HANDLER)
        manifest_start = text.index("def _survey_section_manifest")
        manifest_end = text.index("def _survey_section_excerpt", manifest_start)
        manifest_block = text[manifest_start:manifest_end]
        self.assertIn("signal_terms", manifest_block)
        self.assertIn("citation_count_hint", manifest_block)
        self.assertIn("named_entities_hint", manifest_block)
        self.assertIn("table_figure_refs", manifest_block)

    def test_planner_prompt_uses_manifest_not_fulltext_chunks(self):
        text = read(HANDLER)
        planner_start = text.index("def _plan_survey_cards")
        planner_end = text.index("def _normalize_survey_card_plan", planner_start)
        planner_block = text[planner_start:planner_end]
        self.assertIn("Section manifest", planner_block)
        self.assertIn("section_id", planner_block)
        self.assertIn("section_index", planner_block)
        self.assertIn("expected_output_fields", planner_block)
        self.assertIn("evidence_reason", planner_block)
        self.assertIn("representative_methods", planner_block)
        self.assertNotIn("Chunk text", planner_block)
        self.assertNotIn("chunk_id", planner_block)

    def test_card_generation_uses_intro_and_group_schema(self):
        text = read(HANDLER)
        generator_start = text.index("def _generate_survey_card")
        generator_end = text.index("def _survey_task_context", generator_start)
        generator_block = text[generator_start:generator_end]
        self.assertIn("Intro context", generator_block)
        self.assertIn("Selected section text", generator_block)
        self.assertIn("def _survey_intro_context", text)
        self.assertIn("def _ensure_survey_prerequisite_task", text)
        self.assertIn("prerequisite_card", text)
        self.assertIn("_survey_card_output_schema", text)
        self.assertIn('"items"', text)
        self.assertIn("core_mechanism", text)
        self.assertIn("specific_solution", text)
        self.assertIn("used_by_methods", text)
        self.assertIn("paper_examples", text)
        self.assertIn("dataset_type", text)

    def test_frontend_supports_partial_card_progress(self):
        text = read(APP_JS)
        self.assertIn("readingMapCardProgress", text)
        self.assertIn("reading_map_card_progress", text)
        self.assertIn("function hasPartialReadingMapContent", text)
        self.assertIn("function readingMapCardProgressText", text)
        self.assertIn("failed_partial", text)
        self.assertIn("prerequisite_card", text)
        self.assertIn("field_questions", text)
        self.assertIn("anchor_works", text)

    def test_frontend_survey_cards_do_not_use_item_titles(self):
        text = read(APP_JS)
        render_start = text.index("function renderReadingMapCard")
        render_end = text.index("function scrollReaderToSection", render_start)
        render_block = text[render_start:render_end]
        self.assertNotIn("`Item ${index + 1}`", render_block)
        self.assertIn("function readingMapCardTitle", render_block)
        self.assertIn("function guideCardFieldOrder", text)
        self.assertIn("paper_method_table", text)
        self.assertIn("dataset_catalog", text)
        self.assertIn("challenge_card", text)
        self.assertIn("function readingMapFailureText", text)
        self.assertIn("state.readingMapError", text)
        self.assertIn("function readingMapCardFields", text)
        self.assertIn("论文中的具体例子", text)
        self.assertIn("edgeandtopologyevolution", text)


if __name__ == "__main__":
    unittest.main()
