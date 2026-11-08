"""Tests for scan-sessions.py, the friction scorer.

Run: python3 -m pytest models/models/skills/reflect/tests
No third-party deps beyond pytest; sessions are synthesized per test.
"""
import importlib.util
import json
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scan-sessions.py"


def _load():
    spec = importlib.util.spec_from_file_location("scan_sessions", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan = _load()


# --- session builders ------------------------------------------------------

def asst_tool(name, inp):
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def user_text(text, **extra):
    row = {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}
    row.update(extra)
    return row


def tool_result(text):
    return {"type": "user",
            "message": {"content": [{"type": "tool_result", "content": text}]}}


def write(tmp_path, rows, name="s.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return str(p)


# --- correction lexicon ----------------------------------------------------

@pytest.mark.parametrize("text", [
    "no, that's the wrong paper",
    "no. use the other one",
    "revert that change",
    "rollback the migration",
    "roll back to v1",
    "backtrack, wrong approach",
    "back out those edits",
    "actually, use Postgres",
    "stop the server",
    "I don't want that",
    "that's not what I meant",
])
def test_correction_matches(text):
    assert scan.CORRECTION_RE.search(text)


@pytest.mark.parametrize("text", [
    "the node count is high",       # 'no' inside a word
    "nothing is wrong here",        # 'no' inside a word
    "feedback on the design",       # 'back' inside a word
    "please proceed with plan A",   # no correction token
])
def test_correction_non_matches(text):
    assert not scan.CORRECTION_RE.search(text)


# --- signals ---------------------------------------------------------------

def test_error_signal_counted(tmp_path):
    rows = [asst_tool("Bash", {"command": "cargo build"}),
            tool_result("error[E0382]: borrow of moved value")]
    r = scan.score_session(write(tmp_path, rows))
    assert r["signals"]["errors"] == 1
    assert any(mo["kind"] == "error" for mo in r["moments"])


def test_correction_only_after_agent_action(tmp_path):
    # first human turn is the task, not a correction
    opening = scan.score_session(write(tmp_path, [user_text("no, build it differently")]))
    assert opening["signals"]["corrections"] == 0
    # same words after an assistant turn do count
    rows = [asst_tool("Read", {"file_path": "/a"}), user_text("no, build it differently")]
    after = scan.score_session(write(tmp_path, rows, name="after.jsonl"))
    assert after["signals"]["corrections"] == 1


@pytest.mark.parametrize("injected", [
    user_text("<system-reminder>don't do that</system-reminder>"),
    user_text("<task-notification>agent stopped</task-notification>"),
    user_text("revert this", isMeta=True),
])
def test_injected_turns_not_corrections(tmp_path, injected):
    rows = [asst_tool("Read", {"file_path": "/a"}), injected]
    r = scan.score_session(write(tmp_path, rows))
    assert r["signals"]["corrections"] == 0


def test_revisit_across_tool_types(tmp_path):
    rows = ([asst_tool("Grep", {"pattern": "mixnet latency"})] * 4
            + [asst_tool("WebFetch", {"url": "https://example.org/p"})] * 3
            + [asst_tool("Read", {"file_path": "/notes.md"})] * 3)
    r = scan.score_session(write(tmp_path, rows))
    # 4-2 (grep) + 3-2 (fetch) + 3-2 (read) = 4
    assert r["signals"]["read_repeats"] == 4
    kinds = {mo["excerpt"].split("x ", 1)[1] for mo in r["moments"] if mo["kind"] == "read_repeat"}
    assert kinds == {"mixnet latency", "https://example.org/p", "/notes.md"}


# --- moment anchoring ------------------------------------------------------

def test_every_moment_has_a_real_line_and_anchors_survive_cap(tmp_path):
    rows = [asst_tool("Grep", {"pattern": "x"}) for _ in range(4)]  # a read_repeat anchor
    rows += [tool_result("error: boom") for _ in range(30)]          # 30 error hits > cap
    r = scan.score_session(write(tmp_path, rows))
    assert len(r["moments"]) <= scan.MOMENT_CAP
    assert all(mo["line"] >= 1 for mo in r["moments"])               # no line:0 sentinel
    assert any(mo["kind"] == "read_repeat" for mo in r["moments"])   # anchor kept despite cap


# --- date parsing ----------------------------------------------------------

def test_date_only_until_spans_whole_day():
    span = scan.parse_date("2026-10-13", end=True) - scan.parse_date("2026-10-13")
    assert span == 86399  # 24h minus one second


def test_z_timestamp_parses():
    assert isinstance(scan.parse_date("2026-01-01T00:00:00Z"), float)


# --- robustness ------------------------------------------------------------

def test_null_message_and_malformed_lines_do_not_crash(tmp_path):
    p = tmp_path / "drift.jsonl"
    p.write_text("\n".join([
        json.dumps({"type": "user", "message": None}),       # null message
        "not json at all",                                    # malformed line
        json.dumps({"type": "assistant", "message": {"content": "plain string"}}),
        json.dumps(tool_result("error here")),
    ]))
    r = scan.score_session(str(p))
    assert r["signals"]["errors"] == 1  # survived the bad lines, still scored the good one


# --- error signal precision ---------------------------------------------------

def asst_bash(use_id, command):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": use_id, "name": "Bash", "input": {"command": command}}]}}


def result_for(use_id, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": use_id, "content": text}]}}


def test_error_word_inside_a_diff_dump_is_not_an_error(tmp_path):
    dump = "- model: old\n+ model: new\n" + "context line mentioning error handling\n" * 40
    rows = [asst_bash("t1", "git diff"), result_for("t1", dump)]
    r = scan.score_session(write(tmp_path, rows))
    assert r["signals"]["errors"] == 0


def test_error_word_inside_a_long_command_output_is_not_an_error(tmp_path):
    out = "Updated expert X\n" + "note: error budget unchanged\n" * 40
    rows = [asst_bash("t1", "just apply hopr"), result_for("t1", out)]
    r = scan.score_session(write(tmp_path, rows))
    assert r["signals"]["errors"] == 0


@pytest.mark.parametrize("text", [
    "Exit code 1\nsomething broke",
    "PreToolUse:Bash hook error: git-guard: blocked",
    "zsh: command not found: foo",
    "fatal: not a git repository",
])
def test_line_anchored_failure_markers_count_even_for_read_commands(tmp_path, text):
    rows = [asst_bash("t1", "cat file"), result_for("t1", text)]
    r = scan.score_session(write(tmp_path, rows))
    assert r["signals"]["errors"] == 1


def test_short_loose_error_still_counts_for_a_normal_command(tmp_path):
    rows = [asst_bash("t1", "make build"), result_for("t1", "build failed in step 2")]
    r = scan.score_session(write(tmp_path, rows))
    assert r["signals"]["errors"] == 1
