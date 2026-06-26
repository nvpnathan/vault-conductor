from vault_conductor import cmux
from vault_conductor.cmux import (
    CmuxAdapter,
    CmuxCapabilities,
    CmuxHITLPolicy,
    CmuxRuntimeState,
    CmuxTarget,
    CmuxWorkspaceLayout,
)

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


def test_capabilities_parse_live_methods_shape():
    capabilities = CmuxCapabilities.from_json(
        {
            "protocol": "cmux-socket",
            "methods": ["system.identify", "workspace.create", "browser.snapshot"],
        }
    )

    assert capabilities.supports("system.identify")
    assert capabilities.supports("workspace.create")
    assert capabilities.supports("browser.snapshot")


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


def test_workspace_layout_prefers_top_level_session_refs_when_layout_drifts():
    restored = CmuxWorkspaceLayout.from_session(
        {
            "workspace_ref": "workspace:9",
            "surface_ref": "surface:10",
            "cmux_layout": {
                "workspace_ref": "workspace:1",
                "surfaces": {"agent": "surface:1", "run_note": "surface:2"},
            },
        }
    )

    assert restored.workspace_ref == "workspace:9"
    assert restored.agent_surface_ref == "surface:10"
    assert restored.run_note_surface_ref == "surface:2"


def test_runtime_state_loads_session_layouts_and_indexes_workspaces():
    state = CmuxRuntimeState.from_sessions_data(
        {
            "version": 1,
            "sessions": {
                "AGT-0001": {
                    "task_id": "AGT-0001",
                    "run_id": "AGT-0001-RUN-001",
                    "workspace_ref": "workspace:1",
                    "surface_ref": "surface:1",
                    "status": "running",
                    "cmux_layout": {
                        "workspace_ref": "workspace:1",
                        "workspace_id": "workspace-id-1",
                        "surfaces": {
                            "agent": "surface:1",
                            "run_note": "surface:2",
                            "task_note": "surface:3",
                        },
                    },
                }
            },
        }
    )

    session = state.sessions["AGT-0001"]
    assert session.task_id == "AGT-0001"
    assert session.run_id == "AGT-0001-RUN-001"
    assert session.workspace_ref == "workspace:1"
    assert session.layout.workspace_id == "workspace-id-1"
    assert session.layout.agent_surface_ref == "surface:1"
    assert state.workspace_refs() == {"workspace:1"}
    assert state.find_by_workspace("workspace:1") == session


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


def test_durable_asset_root_prefers_configured_root_and_sanitizes_branch(tmp_path, monkeypatch):
    preferred = tmp_path / "preferred-assets"
    fallback = tmp_path / "repo"
    monkeypatch.setenv("VAULT_CONDUCTOR_ASSET_ROOT", str(preferred))

    root = cmux.durable_asset_root("feature/review browser", "AGT-0001", fallback_root=fallback)

    assert root == preferred / "feature-review-browser" / "AGT-0001"
    assert root.is_dir()


def test_adapter_opens_browser_in_reused_helper_pane(fake_cmux, tmp_path):
    adapter = CmuxAdapter()
    workspace_ref = adapter.new_workspace(name="AGT-0001", description="Review", cwd=tmp_path, command="sleep 1")
    layout = CmuxWorkspaceLayout(workspace_ref=workspace_ref, surfaces={"agent": "surface:1"})

    first = adapter.open_browser_in_helper(layout, "https://github.test/demo/pull/1")
    second = adapter.open_browser_in_helper(first, "https://github.test/demo/pull/1/files")

    calls = cmux_calls(fake_cmux)
    browser_pane_calls = [call for call in calls if call[:4] == ["new-pane", "--type", "browser", "--direction"]]
    browser_surface_calls = [call for call in calls if call[:1] == ["new-surface"] and "--type" in call and "browser" in call]
    assert first.panes["helper"] == "pane:2"
    assert first.review_browser_surface_ref == "surface:2"
    assert second.panes["helper"] == "pane:2"
    assert second.review_browser_surface_ref == "surface:3"
    assert len(browser_pane_calls) == 1
    assert browser_pane_calls[0][-2:] == ["--focus", "false"]
    assert browser_surface_calls == [
        [
            "new-surface",
            "--workspace",
            workspace_ref,
            "--pane",
            "pane:2",
            "--type",
            "browser",
            "--url",
            "https://github.test/demo/pull/1/files",
            "--focus",
            "false",
        ]
    ]


def test_adapter_captures_browser_snapshot_and_screenshot(fake_cmux, tmp_path):
    adapter = CmuxAdapter()
    screenshot = tmp_path / "browser.png"

    snapshot = adapter.browser_snapshot("surface:2")
    result = adapter.browser_screenshot("surface:2", screenshot)

    calls = cmux_calls(fake_cmux)
    assert snapshot["surface_ref"] == "surface:2"
    assert snapshot["title"] == "Fake PR"
    assert result == screenshot
    assert screenshot.read_text(encoding="utf-8") == "fake screenshot"
    assert any(call[:3] == ["--json", "browser", "surface:2"] and "snapshot" in call for call in calls)
    assert any(call[:3] == ["browser", "surface:2", "screenshot"] and str(screenshot) in call for call in calls)


def test_present_handoff_respects_non_disruptive_focus_policy(fake_cmux):
    adapter = CmuxAdapter()
    layout = CmuxWorkspaceLayout(workspace_ref="workspace:1", surfaces={"agent": "surface:1"})

    adapter.present_handoff(layout, pr_url="https://github.test/demo/pull/1")

    calls = cmux_calls(fake_cmux)
    assert any(call[:4] == ["new-pane", "--type", "browser", "--direction"] for call in calls)
    assert not any(call[:1] == ["select-workspace"] for call in calls)
    assert not any("--focus" in call and call[call.index("--focus") + 1] == "true" for call in calls)
