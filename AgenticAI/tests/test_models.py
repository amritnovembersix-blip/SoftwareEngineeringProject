from agentic.models import word_count


def test_word_count():
    assert word_count("  a\tb\nc  ") == 3
    assert word_count(" \t\n") == 0
    assert word_count("one two three four five six seven eight nine ten eleven twelve\n") == 12
