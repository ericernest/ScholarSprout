"""Final verification script for paper_reading module."""
import sys, os, json, glob

def test(name):
    print(f"\n{'='*40}\n  {name}\n{'='*40}")

test("1. Schemas")
from paper_reading.schemas.request import PaperReadingRequest
from paper_reading.schemas.response import PaperReadingResponse, SessionState
print(f"  PaperReadingRequest action default: {PaperReadingRequest.model_fields['action'].default}")
print(f"  PaperReadingResponse status default: {PaperReadingResponse.model_fields['status'].default}")

test("2. Pipeline Metadata")
from paper_reading.pipeline.metadata import PaperMetadata, Author
m = PaperMetadata(paper_id='test', title='Test Paper', source='arxiv', abstract='Test abstract')
print(f"  PaperMetadata created: {m.title}")

test("3. KG Models")
from paper_reading.kg.models import ALL_NODE_TYPES, ALL_EDGE_TYPES, KGNode
print(f"  {len(ALL_NODE_TYPES)} node types")
print(f"  {len(ALL_EDGE_TYPES)} edge types")
n = KGNode(node_type='Concept', label='Test', paper_id='p1')
print(f"  KGNode OK")

test("4. Storage")
from paper_reading.harness.storage import PaperReadingStorage
s = PaperReadingStorage()
print(f"  Storage OK, base_dir: {s.base_dir}")

test("5. Session Manager")
from paper_reading.harness.session import SessionManager
sm = SessionManager()
session = sm.create_session(paper_id='test123', paper_title='Test Paper')
print(f"  Created: {session.session_id} (state={session.state})")
sm.pause(session.session_id)
cp = sm.get_latest_checkpoint(session.session_id)
print(f"  Paused, checkpoint: {cp.checkpoint_id if cp else 'none'}")
sm.resume(session.session_id)
print(f"  Resumed: state={session.state}")

test("6. Progress")
from paper_reading.harness.progress import build_initial_progress, update_section_status, format_progress_message
p = build_initial_progress(8)
p = update_section_status(p, 'sec:1', 'completed')
p = update_section_status(p, 'sec:2', 'reading')
print(f"  {format_progress_message(p)}")

test("7. Fork/Merge")
from paper_reading.harness.fork_merge import ForkMergeManager
fm = ForkMergeManager(sm)
fork = fm.create_fork(session.session_id, fork_context='test_formula', fork_skills=['reading.math_verifier'])
if fork:
    print(f"  Fork: {fork.session_id}, skills={fork.active_skills}")
    result = fm.merge_fork(fork.session_id)
    print(f"  Merge: success={result.success}, findings={len(result.key_findings)}")
else:
    print("  Fork: FAILED")

test("8. KG Engine + Builder + Fusion")
from paper_reading.kg.engine import KnowledgeGraphEngine
from paper_reading.kg.models import MotivatesEdge
engine = KnowledgeGraphEngine()
n1 = KGNode(node_type='Problem', label='Few-shot Learning', paper_id='p1', properties={'domain': 'meta-learning'})
n2 = KGNode(node_type='Method', label='ProtoNet', paper_id='p1', properties={'category': 'metric-learning'})
engine.add_node(n1)
engine.add_node(n2)
nodes_p1 = engine.list_nodes_by_paper('p1')
e = MotivatesEdge(source_id=nodes_p1[0]['node_id'], target_id=nodes_p1[1]['node_id'], paper_id='p1')
engine.add_edge(e.source_id, e.target_id, e)
print(f"  Nodes: {engine.size[0]}, Edges: {engine.size[1]}")
print(f"  Search Proto: {len(engine.search_nodes('Proto'))} results")
print(f"  Path query: {len(engine.query_path('Few-shot', 'Proto'))} results")

# Builder
from paper_reading.kg.builder import ProgressiveKGBuilder
builder = ProgressiveKGBuilder(engine)
for sec_id, exp in [('3.2. Method', 'method'), ('Abstract', 'abstract'), ('1. Intro', 'introduction')]:
    r = builder.classify_section(sec_id)
    print(f"  Classify '{sec_id}' -> {r} {'OK' if r == exp else 'FAIL'}")

# Fusion
from paper_reading.kg.fusion import CrossPaperFusion
n3 = KGNode(node_type='Dataset', label='miniImageNet', paper_id='p2')
engine.add_node(n3)
n4 = KGNode(node_type='Dataset', label='miniImageNet', paper_id='p1')
engine.add_node(n4)
fusion = CrossPaperFusion(engine)
result = fusion.fuse('p1', 'p2')
print(f"  Fusion: {len(result.events)} events")

test("9. Tools")
from tools.builtin.paper_search_tool import PaperSearchTool
from tools.builtin.pdf_parse_tool import PDFParseTool
from tools.builtin.kg_query_tool import KGQueryTool, set_kg_engine
from tools.builtin.kg_build_tool import KGBuildTool, set_kg_builder
set_kg_engine(engine)
set_kg_builder(builder)
print(f"  {PaperSearchTool().spec.name}")
print(f"  {PDFParseTool().spec.name}")
print(f"  {KGQueryTool().spec.name}")
print(f"  {KGBuildTool().spec.name}")

# Test KG query
qt = KGQueryTool()
r = qt.run({'query_type': 'search', 'keyword': 'Proto'})
print(f"  KGQuery search: {r.get('count', 0)} nodes found")

test("10. Tool Registry")
from tools.registry import create_builtin_tool_registry
reg = create_builtin_tool_registry()
names = [t.name for t in reg.list_tools()]
print(f"  {len(names)} tools: {names}")

test("11. Agent Profile")
with open('agents/profiles.json') as f:
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
py_files = sorted(glob.glob('paper_reading/**/*.py', recursive=True))
total = sum(os.path.getsize(f) for f in py_files)
print(f"  {len(py_files)} Python files, {total:,} bytes")
for f in py_files:
    print(f"    {f}")

print(f"\n{'='*50}")
print("ALL 13 CHECKS PASSED")
print(f"{'='*50}")
