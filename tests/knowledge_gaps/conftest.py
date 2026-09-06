"""Shared fixtures for ``tests/knowledge_gaps``."""

from __future__ import annotations

import pytest

from tests.knowledge_gaps.helpers import make_organism


@pytest.fixture
def organism(db_session):
    return make_organism(db_session)
