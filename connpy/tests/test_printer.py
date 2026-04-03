"""Tests for connpy.printer module."""
import sys
from io import StringIO
from connpy import printer


class TestPrinter:
    def test_info_output(self, capsys):
        printer.info("hello world")
        captured = capsys.readouterr()
        assert "[i] hello world" in captured.out

    def test_success_output(self, capsys):
        printer.success("done")
        captured = capsys.readouterr()
        assert "[✓] done" in captured.out

    def test_warning_output(self, capsys):
        printer.warning("careful")
        captured = capsys.readouterr()
        assert "[!] careful" in captured.out

    def test_error_output(self, capsys):
        printer.error("failed")
        captured = capsys.readouterr()
        assert "[✗] failed" in captured.err

    def test_debug_output(self, capsys):
        printer.debug("debug info")
        captured = capsys.readouterr()
        assert "[d] debug info" in captured.out

    def test_start_output(self, capsys):
        printer.start("starting")
        captured = capsys.readouterr()
        assert "[+] starting" in captured.out

    def test_custom_output(self, capsys):
        printer.custom("TAG", "custom message")
        captured = capsys.readouterr()
        assert "[TAG] custom message" in captured.out

    def test_multiline_indentation(self, capsys):
        printer.info("line1\nline2\nline3")
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert lines[0] == "[i] line1"
        # Second line should be indented by len("[i] ") = 4 chars
        assert lines[1].startswith("    line2")
        assert lines[2].startswith("    line3")
