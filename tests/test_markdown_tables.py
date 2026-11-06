"""Table formatting preserves content, alignment intent and fenced examples."""
import pytest

from scripts.markdown_tables import cells, format_tables


def test_alignment_and_idempotence():
    source = "| Name | Value |\n| :--- | ---: |\n| a much longer name | 3 |\n"
    result = format_tables(source)
    assert format_tables(result) == result
    assert [cells(row) for row in result.splitlines()][::2] == [
        cells(row) for row in source.splitlines()][::2]
    positions = [[i for i, char in enumerate(row) if char == "|"] for row in result.splitlines()]
    assert positions[0] == positions[1] == positions[2]
    assert cells(result.splitlines()[1])[0].startswith(":")
    assert cells(result.splitlines()[1])[1].endswith(":")


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_fenced_examples_untouched(fence):
    source = f"{fence}text\n| x | y |\n| --- | --- |\n| long | z |\n{fence}\n"
    assert format_tables(source) == source


def test_escaped_pipe_and_missing_outer_pipes():
    source = "A | B\n--- | :---:\n| `x\\|y` | z |"
    result = format_tables(source)
    assert cells(result.splitlines()[2]) == ["`x\\|y`", "z"]
    assert result.startswith("| ") and result.endswith(" |")
    assert not result.endswith("\n")


def test_malformed_table_reported():
    with pytest.raises(ValueError, match="column count at line 1"):
        format_tables("| x | y |\n| --- | --- |\n| z |\n")


def test_prose_is_unchanged():
    text = "# Document\n\nA | B is a notation, not a table.\n"
    assert format_tables(text) == text


def test_indented_table_and_body_without_outer_pipes():
    source = "  A | B\n  --- | ---\n  long | short\n"
    result = format_tables(source)
    assert all(line.startswith('  | ') and line.endswith(' |') for line in result.splitlines())
    assert format_tables(result) == result


def test_even_backslashes_do_not_escape_column_separator():
    assert cells(r'| path\\ | next |') == [r'path\\', 'next']
