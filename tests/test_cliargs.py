import pytest

from svngit.cliargs import parse
from svngit.errors import UsageError


def test_long_option_with_separate_value():
    opts = parse(["--message", "hello"], values=["message"])
    assert opts.get("message") == "hello"
    assert opts.positionals == []


def test_long_option_with_equals():
    opts = parse(["--message=hello world"], values=["message"])
    assert opts.get("message") == "hello world"


def test_short_option_attached_value():
    opts = parse(["-mhello"], values=["m"])
    assert opts.get("m") == "hello"


def test_bundled_flags_then_value():
    # `git commit -am "msg"` is the case that argparse cannot express.
    opts = parse(["-am", "msg"], flags=["a"], values=["m"])
    assert opts.has("a")
    assert opts.get("m") == "msg"


def test_double_dash_separates_paths():
    opts = parse(["--cached", "--", "-weird-name.txt"], flags=["cached"])
    assert opts.has("cached")
    assert opts.after_dashdash == ["-weird-name.txt"]
    assert opts.paths == ["-weird-name.txt"]


def test_paths_falls_back_to_positionals():
    opts = parse(["a.txt", "b.txt"])
    assert opts.paths == ["a.txt", "b.txt"]


def test_no_prefix_disables_flag():
    opts = parse(["--no-rebase"], flags=["rebase"])
    assert not opts.has("rebase")


def test_numeric_shorthand():
    opts = parse(["-5"], allow_numeric=True)
    assert opts.get("n") == "5"


def test_repeated_flag_counts():
    opts = parse(["-v", "-v"], flags=["v"])
    assert opts.count("v") == 2


def test_unknown_option_is_a_usage_error():
    with pytest.raises(UsageError):
        parse(["--nope"], flags=["yes"])


def test_missing_value_is_a_usage_error():
    with pytest.raises(UsageError):
        parse(["--message"], values=["message"])


def test_first_returns_earliest_present_alias():
    opts = parse(["-m", "x"], values=["message", "m"])
    assert opts.first("message", "m") == "x"
    assert opts.first("absent", default="fallback") == "fallback"
