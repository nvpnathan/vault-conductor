from vault_conductor import cmux
from vault_conductor.cmux import CmuxAdapter, CmuxHITLPolicy, CmuxTarget, CmuxWorkspaceLayout

from conftest import cmux_calls


def test_adapter_runs_json_commands_with_socket_and_id_format(fake_cmux):
    adapter = CmuxAdapter(socket_path="/tmp/cmux-test.sock")

    result = adapter.run("identify", json_mode=True, id_format="both")

    calls = cmux_calls(fake_cmux)
    assert result.ok is True
    assert result.parsed_json["workspace_ref"] == "workspace:1"
    assert result.parsed_json["workspace_id"] == "workspace-id-1"
    assert result.command == [
        "cmux",
        "--socket",
        "/tmp/cmux-test.sock",
        "--json",
        "--id-format",
        "both",
        "identify",
    ]
    assert calls[0] == ["--socket", "/tmp/cmux-test.sock", "--json", "--id-format", "both", "identify"]


def test_adapter_discovers_capabilities_and_current_target(fake_cmux):
    adapter = CmuxAdapter(socket_path="/tmp/cmux-test.sock")

    capabilities = adapter.capabilities()
    target = adapter.identify()

    assert capabilities.supports("identify")
    assert capabilities.supports("new-workspace")
    assert target == CmuxTarget(
        workspace_ref="workspace:1",
        workspace_id="workspace-id-1",
        surface_ref="surface:1",
        surface_id="surface-id-1",
        socket_path="/tmp/cmux-test.sock",
    )


def test_hitl_policy_defaults_to_non_disruptive_focus():
    policy = CmuxHITLPolicy.non_disruptive()
    handoff = CmuxHITLPolicy.interrupt_for_handoff()

    assert policy.focus_new_surfaces is False
    assert policy.browser_focus is False
    assert policy.allow_select_workspace is False
    assert policy.notify is True
    assert policy.focus_value() == "false"
    assert handoff.focus_new_surfaces is True
    assert handoff.browser_focus is True
    assert handoff.allow_select_workspace is True
    assert handoff.focus_value(browser=True) == "true"


def test_workspace_layout_round_trips_session_patch_and_legacy_records():
    layout = CmuxWorkspaceLayout(
        workspace_ref="workspace:1",
        workspace_id="workspace-id-1",
        panes={"helper": "pane:2"},
        surfaces={"agent": "surface:1", "run_note": "surface:2", "task_note": "surface:3"},
        target=CmuxTarget(workspace_ref="workspace:1", socket_path="/tmp/cmux-test.sock"),
    )

    patch = layout.to_session_patch()
    restored = CmuxWorkspaceLayout.from_session(patch)
    legacy = CmuxWorkspaceLayout.from_session({"workspace_ref": "workspace:9", "surface_ref": "surface:7"})

    assert patch["workspace_ref"] == "workspace:1"
    assert patch["workspace_id"] == "workspace-id-1"
    assert patch["surface_ref"] == "surface:1"
    assert patch["cmux_layout"]["surfaces"]["run_note"] == "surface:2"
    assert patch["cmux_layout"]["panes"]["helper"] == "pane:2"
    assert restored == layout
    assert legacy.workspace_ref == "workspace:9"
    assert legacy.agent_surface_ref == "surface:7"


def test_legacy_module_wrappers_keep_existing_shapes(fake_cmux):
    assert cmux.ping() is True
    assert cmux.cmux_json("identify")["workspace_ref"] == "workspace:1"

    out, rc = cmux.run_cmux("ping")

    assert out == "OK"
    assert rc == 0


def test_adapter_captures_review_artifact_without_stealing_focus(fake_cmux, tmp_path):
    adapter = CmuxAdapter()
    layout = CmuxWorkspaceLayout(workspace_ref="workspace:1", surfaces={"agent": "surface:1"})
    artifact_path = tmp_path / "artifacts" / "AGT-0001-review.html"

    result = adapter.capture_review_artifact(
        artifact_path,
        title="AGT-0001 Review Evidence",
        evidence={"status": "failed", "output": "<script>bad()</script>", "files": ["a.py", "b.py"]},
        layout=layout,
    )

    html = artifact_path.read_text(encoding="utf-8")
    calls = cmux_calls(fake_cmux)
    assert result == artifact_path
    assert "AGT-0001 Review Evidence" in html
    assert "&lt;script&gt;bad()&lt;/script&gt;" in html
    assert "&quot;a.py&quot;" in html
    assert calls[-1][:4] == ["new-pane", "--type", "browser", "--direction"]
    assert "file://" in " ".join(calls[-1])
    assert calls[-1][-2:] == ["--focus", "false"]
