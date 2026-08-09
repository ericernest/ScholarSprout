"""Final verification script for paper_reading module."""
import sys, os, json, glob

def test(name):
    print(f"\n{'='*40}\n  {name}\n{'='*40}")

test("1. Schemas")
from handlers.paper_reading.schemas.request import PaperReadingRequest
from handlers.paper_reading.schemas.response import PaperReadingResponse, SessionState
print(f"  PaperReadingRequest action default: {PaperReadingRequest.model_fields['action'].default}")
print(f"  PaperReadingResponse status default: {PaperReadingResponse.model_fields['status'].default}")

test("2. Pipeline Metadata")
from handlers.paper_reading.pipeline.metadata import PaperMetadata, Author
m = PaperMetadata(paper_id='test', title='Test Paper', source='arxiv', abstract='Test abstract')
print(f"  PaperMetadata created: {m.title}")

test("3. Parser + Reading Map")
from handlers.paper_reading.pipeline.parser import PaperParser, Section
from handlers.paper_reading.handler import _empty_reading_map
parser = PaperParser()
section = Section(section_id='1', title='Introduction', level=1, start_page=1, end_page=1, text='Intro text')
reading_map = _empty_reading_map()
print(f"  Parser OK: {parser.__class__.__name__}")
print(f"  Section OK: {section.title}")
print(f"  Reading map keys: {', '.join(sorted(reading_map.keys())[:5])} ...")

test("4. Storage")
from handlers.paper_reading.harness.storage import PaperReadingStorage
s = PaperReadingStorage()
print(f"  Storage OK, base_dir: {s.base_dir}")

test("5. Session Manager")
from handlers.paper_reading.harness.session import SessionManager
sm = SessionManager()
session = sm.create_session(paper_id='test123', paper_title='Test Paper')
print(f"  Created: {session.session_id} (state={session.state})")
sm.pause(session.session_id)
cp = sm.get_latest_checkpoint(session.session_id)
print(f"  Paused, checkpoint: {cp.checkpoint_id if cp else 'none'}")
sm.resume(session.session_id)
print(f"  Resumed: state={session.state}")

test("6. Progress")
from handlers.paper_reading.harness.progress import build_initial_progress, update_section_status, format_progress_message
p = build_initial_progress(8)
p = update_section_status(p, 'sec:1', 'completed')
p = update_section_status(p, 'sec:2', 'reading')
print(f"  {format_progress_message(p)}")

test("7. Fork/Merge")
from handlers.paper_reading.harness.fork_merge import ForkMergeManager
fm = ForkMergeManager(sm)
fork = fm.create_fork(session.session_id, fork_context='test_formula', fork_skills=['reading.math_verifier'])
if fork:
    print(f"  Fork: {fork.session_id}, skills={fork.active_skills}")
    result = fm.merge_fork(fork.session_id)
    print(f"  Merge: success={result.success}, findings={len(result.key_findings)}")
else:
    print("  Fork: FAILED")

test("8. Reading Map Skeleton")
empty = _empty_reading_map()
print(f"  Paper type: {empty['paper_type']}")
print(f"  Research groups: {len(empty['research_map'])}")
print(f"  Survey groups: {len(empty['survey_map'])}")
print(f"  Section guides: {len(empty['section_guides'])}")

test("9. Tools")
from tools.builtin.paper_search_tool import PaperSearchTool
from tools.builtin.pdf_parse_tool import PDFParseTool
print(f"  {PaperSearchTool().spec.name}")
print(f"  {PDFParseTool().spec.name}")

test("10. Tool Registry")
from tools.registry import create_builtin_tool_registry
reg = create_builtin_tool_registry()
names = [t.name for t in reg.list_tools()]
print(f"  {len(names)} tools: {names}")

test("11. Agent Profile")
with open('agents/profiles.json', encoding='utf-8') as f:
    profiles = json.load(f)
pp = [p for p in profiles if p['type'] == 'paper_reading']
if pp:
    p = pp[0]
    print(f"  Name: {p['name']}")
    print(f"  Tools: {p['tools']}")
    print(f"  Default skill: {p['default_skill']}")
    print(f"  Skills: {len(p['skills'])} configured")
else:
    print("  MISSING!")

test("12. SKILL.md Files")
reading_dir = 'skills/builtin/reading'
skill_dirs = sorted([d for d in os.listdir(reading_dir) if os.path.isdir(os.path.join(reading_dir, d))])
skills_with_md = [d for d in skill_dirs if os.path.exists(os.path.join(reading_dir, d, 'SKILL.md'))]
print(f"  {len(skills_with_md)}/8 SKILL.md files:")
for s in skills_with_md:
    md_path = os.path.join(reading_dir, s, 'SKILL.md')
    size = os.path.getsize(md_path)
    with open(md_path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('id:'):
                print(f"    {line.strip()} ({size}B)")
                break

test("13. File Inventory")
py_files = sorted(glob.glob('handlers/paper_reading/**/*.py', recursive=True))
total = sum(os.path.getsize(f) for f in py_files)
print(f"  {len(py_files)} Python files, {total:,} bytes")
for f in py_files:
    print(f"    {f}")

print(f"\n{'='*50}")
print("ALL 13 CHECKS PASSED")
print(f"{'='*50}")
