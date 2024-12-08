import pytest

try:
    import bdx.graph

    HAVE_GRAPHS = True
except ImportError:
    HAVE_GRAPHS = False


@pytest.mark.skipif(
    not HAVE_GRAPHS,
    reason="Graphs not available, install [graphs] optional dependencies",
)
@pytest.mark.parametrize(
    "algorithm",
    [
        "BFS",
        "DFS",
        "ASTAR",
    ],
)
def test_graph_generation(readonly_index, algorithm):
    from bdx.graph import GraphAlgorithm, generate_graph

    graph = generate_graph(
        readonly_index.path.parent,
        "main",
        "path:foo.c.o",
        algo=GraphAlgorithm[algorithm],
    )

    assert "main" in graph.nodes()
    assert "uses_c_function" in graph.nodes()
    assert "c_function" in graph.nodes()

    assert graph.has_edge("main", "uses_c_function")
    assert graph.has_edge("uses_c_function", "c_function")
