# Domain onboarding knowledge graph foundation

The first graph stage is opt-in and shadow-only. Enable it with
`DomainOnboardingConfig(knowledge_graph_enabled=True)`. The default API behavior
is unchanged.

Graphs are built only after the selected onboarding result passes all hard
quality gates. Nodes and edges are derived from the validated output and its
selected paper set; no model call is allowed to invent graph entities.

The validator rejects duplicate node IDs, dangling edges, paper nodes outside
the selected grounded set, and dependency cycles. The graph path planner emits
a topological prerequisite/stage order when validation succeeds and explicitly
falls back to the existing generated learning path otherwise.

This stage does not replace `learning_path`. Promotion from shadow mode requires
offline evaluation of graph validity and route quality, followed by controlled
online tests. Setting `knowledge_graph_shadow_mode=False` is intentionally
rejected in this version.
