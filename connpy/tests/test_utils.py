import pytest
from connpy.utils import log_cleaner

def test_log_cleaner_empty():
    assert log_cleaner("") == ""
    assert log_cleaner(None) == ""

def test_log_cleaner_plain_text():
    assert log_cleaner("hello world") == "hello world"

def test_log_cleaner_ansi_colors():
    # \x1b[31m is red, \x1b[0m is reset
    assert log_cleaner("\x1b[31mhello\x1b[0m world") == "hello world"

def test_log_cleaner_osc_window_title():
    # Set window title OSC: \x1b]0;my title\x07 followed by prompt
    sample = "\x1b]0;fluzzi32@norman: ~\x07fluzzi32@norman:~$"
    assert log_cleaner(sample) == "fluzzi32@norman:~$"

def test_log_cleaner_osc_with_st_terminator():
    # OSC can also be terminated by \x1b\\ (ST)
    sample = "\x1b]0;some title\x1b\\my_prompt>"
    assert log_cleaner(sample) == "my_prompt>"

def test_log_cleaner_mixed_ansi_and_osc():
    sample = "\x1b]0;title\x07\x1b[32muser@host\x1b[0m:\x1b[34m/path\x1b[0m$ "
    assert log_cleaner(sample) == "user@host:/path$"

def test_log_cleaner_carriage_return_and_backspace():
    # Test that standard control sequences like \r and \b still work as expected
    assert log_cleaner("hello\rworld") == "world"
    assert log_cleaner("hell\bo") == "helo"
