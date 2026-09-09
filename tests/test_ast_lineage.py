"""Unit tests for ASTLineageAnalyzer causality extraction."""

from iris.analysis.ast_lineage import ASTLineageAnalyzer


def test_ast_lineage_simple_assignment():
    code = """
x = 10
y = x + 5
z = len(y)
"""
    line_map = ASTLineageAnalyzer.analyze_source(code)
    # Line 2: x = 10
    assert 2 in line_map
    assert "x" in line_map[2].written_vars
    assert len(line_map[2].read_vars) == 0

    # Line 3: y = x + 5
    assert 3 in line_map
    assert "y" in line_map[3].written_vars
    assert "x" in line_map[3].read_vars
    assert line_map[3].operation == "binop:add"

    # Line 4: z = len(y)
    assert 4 in line_map
    assert "z" in line_map[4].written_vars
    assert "y" in line_map[4].read_vars
    assert "len" in line_map[4].read_vars
    assert line_map[4].operation == "call:len"


def test_ast_lineage_augmented_assign():
    code = """
total = 100
total += 15
"""
    line_map = ASTLineageAnalyzer.analyze_source(code)
    assert 3 in line_map
    assert "total" in line_map[3].read_vars
    assert "total" in line_map[3].written_vars
    assert line_map[3].operation == "aug:add"


def test_ast_lineage_attribute_call():
    code = """
clean = text.strip()
"""
    line_map = ASTLineageAnalyzer.analyze_source(code)
    assert 2 in line_map
    assert "clean" in line_map[2].written_vars
    assert "text" in line_map[2].read_vars
    assert line_map[2].operation == "call:strip"
