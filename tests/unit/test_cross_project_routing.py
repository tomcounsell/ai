"""Tests for cross-project routing via find_project_for_chat.

Covers the gap identified in issue #471: no test confirmed that
"Dev: Popoto" resolves to project_key="popoto" vs "Dev: Valor"
to project_key="valor" when multiple projects share the routing map.
"""

import bridge.routing as routing


class TestCrossProjectRouting:
    """Verify find_project_for_chat resolves Dev: group names to the correct project."""

    def _setup_routing(self, sample_config, active_keys=None):
        """Set up routing globals from sample_config fixture."""
        if active_keys is None:
            active_keys = list(sample_config["projects"].keys())
        routing.ACTIVE_PROJECTS = active_keys
        routing.GROUP_TO_PROJECT = routing.build_group_to_project_map(sample_config)

    def test_dev_popoto_resolves_to_popoto(self, sample_config):
        """'Dev: Popoto' chat title should resolve to the popoto project."""
        self._setup_routing(sample_config)
        project = routing.find_project_for_chat("Dev: Popoto")
        assert project is not None
        assert project["_key"] == "popoto"

    def test_dev_valor_resolves_to_valor(self, sample_config):
        """'Dev: Valor' chat title should resolve to the valor project."""
        self._setup_routing(sample_config)
        project = routing.find_project_for_chat("Dev: Valor")
        assert project is not None
        assert project["_key"] == "valor"

    def test_dev_django_template_resolves_to_django(self, sample_config):
        """'Dev: Django Template' chat title should resolve to the django project."""
        self._setup_routing(sample_config)
        project = routing.find_project_for_chat("Dev: Django Template")
        assert project is not None
        assert project["_key"] == "django-project-template"

    def test_unknown_chat_returns_none(self, sample_config):
        """An unrecognized chat title should return None."""
        self._setup_routing(sample_config)
        project = routing.find_project_for_chat("Dev: Unknown Project")
        assert project is None

    def test_none_chat_title_returns_none(self, sample_config):
        """None chat title should return None without raising."""
        self._setup_routing(sample_config)
        project = routing.find_project_for_chat(None)
        assert project is None

    def test_case_insensitive_matching(self, sample_config):
        """Routing should match case-insensitively."""
        self._setup_routing(sample_config)
        # build_group_to_project_map lowercases keys, find_project_for_chat lowercases input
        project = routing.find_project_for_chat("dev: popoto")
        assert project is not None
        assert project["_key"] == "popoto"
