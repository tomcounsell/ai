"""E2E tests for workflow status polling endpoint.

Layer 2: Workflow UI -- verifies the polling endpoint returns valid
responses and reflects workflow state changes.

Run with:
    python tools/testing/browser_test_runner.py apps/podcast/tests/e2e/test_e2e_workflow_polling.py
"""

import pytest
from playwright.sync_api import Page

from apps.podcast.tests.e2e.e2e_helpers import (
    episode_workflow_poll_url,
    episode_workflow_url,
    login_as,
)


@pytest.mark.local_only
class TestPollingEndpointResponse:
    """Verify the status polling endpoint returns valid responses."""

    def test_poll_endpoint_returns_response(self, page: Page, e2e_data, staff_password):
        """Polling endpoint returns a response for a valid episode and step."""
        login_as(page, e2e_data.staff_user.username, staff_password)

        # First visit the workflow page to establish session/cookies
        workflow_url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(workflow_url)
        page.wait_for_load_state("domcontentloaded")

        # Now hit the poll endpoint
        poll_url = episode_workflow_poll_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        response = page.goto(poll_url)

        # Poll endpoint returns 200 (running) or 286 (stop polling)
        assert response.status in (
            200,
            286,
        ), f"Poll endpoint returned unexpected status {response.status}"

    def test_poll_endpoint_returns_html_content(
        self, page: Page, e2e_data, staff_password
    ):
        """Polling endpoint returns HTML with OOB swap elements."""
        login_as(page, e2e_data.staff_user.username, staff_password)

        workflow_url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(workflow_url)
        page.wait_for_load_state("domcontentloaded")

        poll_url = episode_workflow_poll_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(poll_url)
        body_text = page.content()

        # The poll response contains OOB swap divs for sidebar and content
        assert "workflow-sidebar" in body_text or "workflow-step-content" in body_text

    def test_poll_endpoint_for_non_running_returns_286(
        self, page: Page, e2e_data, staff_password
    ):
        """Poll endpoint returns 286 (stop polling) for episodes without running workflow."""
        login_as(page, e2e_data.staff_user.username, staff_password)

        workflow_url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        page.goto(workflow_url)
        page.wait_for_load_state("domcontentloaded")

        # Draft episode has no workflow, so poll should return 286 (stop)
        poll_url = episode_workflow_poll_url(
            e2e_data.podcast.slug, e2e_data.draft_episode.slug, step=1
        )
        response = page.goto(poll_url)
        assert (
            response.status == 286
        ), f"Expected 286 for non-running workflow, got {response.status}"


@pytest.mark.local_only
class TestPollingReflectsState:
    """Verify polling reflects workflow state changes."""

    def test_running_workflow_poll_returns_200(
        self, page: Page, e2e_data, staff_password
    ):
        """Poll returns 200 for an episode with a running workflow."""
        login_as(page, e2e_data.staff_user.username, staff_password)

        # mid_pipeline_episode has status=running
        workflow_url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.mid_pipeline_episode.slug, step=5
        )
        page.goto(workflow_url)
        page.wait_for_load_state("domcontentloaded")

        poll_url = episode_workflow_poll_url(
            e2e_data.podcast.slug, e2e_data.mid_pipeline_episode.slug, step=5
        )
        response = page.goto(poll_url)
        assert (
            response.status == 200
        ), f"Expected 200 for running workflow, got {response.status}"

    def test_poll_response_contains_phase_info(
        self, page: Page, e2e_data, staff_password
    ):
        """Poll response for mid-pipeline episode contains phase-related content."""
        login_as(page, e2e_data.staff_user.username, staff_password)

        workflow_url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.mid_pipeline_episode.slug, step=5
        )
        page.goto(workflow_url)
        page.wait_for_load_state("domcontentloaded")

        poll_url = episode_workflow_poll_url(
            e2e_data.podcast.slug, e2e_data.mid_pipeline_episode.slug, step=5
        )
        page.goto(poll_url)
        body_text = page.content()

        # Response should contain the workflow sidebar with phase names
        assert "Cross-Validation" in body_text or "workflow-sidebar" in body_text

    def test_poll_different_steps_for_same_episode(
        self, page: Page, e2e_data, staff_password
    ):
        """Polling different steps for the same episode all return valid responses."""
        login_as(page, e2e_data.staff_user.username, staff_password)

        # Establish session
        workflow_url = episode_workflow_url(
            e2e_data.podcast.slug, e2e_data.mid_pipeline_episode.slug, step=1
        )
        page.goto(workflow_url)
        page.wait_for_load_state("domcontentloaded")

        for step in [1, 5, 12]:
            poll_url = episode_workflow_poll_url(
                e2e_data.podcast.slug, e2e_data.mid_pipeline_episode.slug, step=step
            )
            response = page.goto(poll_url)
            assert response.status in (
                200,
                286,
            ), f"Poll step {step} returned unexpected {response.status}"
