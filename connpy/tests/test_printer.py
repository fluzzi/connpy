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

    def test_data_output(self, capsys):
        printer.data("my title", "key: value")
        captured = capsys.readouterr()
        # Rich output is formatted with ansi escape sequences or box drawing chars
        # Just check that title and content appear in the output stream
        assert "my title" in captured.out
        assert "key" in captured.out

    def test_node_panel_pass(self, capsys):
        printer.node_panel("node1", "output line\n", 0)
        captured = capsys.readouterr()
        assert "node1" in captured.out
        assert "PASS" in captured.out
        assert "output line" in captured.out

    def test_node_panel_fail(self, capsys):
        printer.node_panel("node2", "error line\n", 1)
        captured = capsys.readouterr()
        assert "node2" in captured.out
        assert "FAIL" in captured.out
        assert "error line" in captured.out

    def test_test_panel(self, capsys):
        printer.test_panel("node1", "output", 0, {"check1": True, "check2": False})
        captured = capsys.readouterr()
        assert "node1" in captured.out
        assert "check1" in captured.out
        assert "check2" in captured.out

    def test_test_summary(self, capsys):
        results = {"node1": {"test1": True}, "node2": {"test2": False}}
        printer.test_summary(results)
        captured = capsys.readouterr()
        assert "node1" in captured.out
        assert "node2" in captured.out
        assert "test1" in captured.out
        assert "test2" in captured.out

    def test_header_output(self, capsys):
        printer.header("My Header")
        captured = capsys.readouterr()
        assert "My Header" in captured.out

    def test_kv_output(self, capsys):
        printer.kv("mykeystring", "myvaluestring")
        captured = capsys.readouterr()
        assert "mykeystring" in captured.out
        assert "myvaluestring" in captured.out

    def test_confirm_action(self, capsys):
        printer.confirm_action("router1", "delete")
        captured = capsys.readouterr()
        assert "[i] delete: router1" in captured.out
