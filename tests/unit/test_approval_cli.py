"""Tests for approval mode CLI integration."""

import pytest

from duh.adapters.approvers import ApprovalMode, TieredApprover
from duh.cli.parser import build_parser
from duh.config import Config


class TestParserApprovalMode:
    def test_default_is_none(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.approval_mode is None

    def test_suggest_mode(self):
        parser = build_parser()
        args = parser.parse_args(["--approval-mode", "suggest"])
        assert args.approval_mode == "suggest"

    def test_auto_edit_mode(self):
        parser = build_parser()
        args = parser.parse_args(["--approval-mode", "auto-edit"])
        assert args.approval_mode == "auto-edit"

    def test_full_auto_mode(self):
        parser = build_parser()
        args = parser.parse_args(["--approval-mode", "full-auto"])
        assert args.approval_mode == "full-auto"

    def test_invalid_mode_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--approval-mode", "yolo"])

    def test_combined_with_other_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "--approval-mode", "auto-edit",
            "--model", "opus",
            "--max-turns", "5",
        ])
        assert args.approval_mode == "auto-edit"
        assert args.model == "opus"
        assert args.max_turns == 5


class TestConfigApprovalMode:
    def test_default_is_empty(self):
        config = Config()
        assert config.approval_mode == ""

    def test_accepts_string(self):
        config = Config(approval_mode="auto-edit")
        assert config.approval_mode == "auto-edit"


class TestApprovalModeFromString:
    def test_suggest(self):
        mode = ApprovalMode("suggest")
        assert mode == ApprovalMode.SUGGEST

    def test_auto_edit(self):
        mode = ApprovalMode("auto-edit")
        assert mode == ApprovalMode.AUTO_EDIT

    def test_full_auto(self):
        mode = ApprovalMode("full-auto")
        assert mode == ApprovalMode.FULL_AUTO

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            ApprovalMode("yolo")


class TestTieredApproverConstruction:
    def test_from_cli_string(self):
        """Verify the full flow: CLI string -> ApprovalMode -> TieredApprover."""
        mode_str = "auto-edit"
        mode = ApprovalMode(mode_str)
        approver = TieredApprover(mode=mode, cwd="/tmp")
        assert approver.mode == ApprovalMode.AUTO_EDIT

    async def test_constructed_approver_works(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        # Read should be auto-approved
        result = await approver.check("Read", {"file_path": "/tmp/x"})
        assert result["allowed"] is True
        # Bash should need approval
        result = await approver.check("Bash", {"command": "ls"})
        assert result["allowed"] is False
