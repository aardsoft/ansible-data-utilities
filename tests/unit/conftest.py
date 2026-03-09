"""pytest fixtures for data-utilities unit tests.

Helper functions live in helpers.py so that test modules can import them
directly without relying on conftest being on sys.path.
"""
import pytest
from helpers import make_plugin, make_valid_keys


@pytest.fixture
def plugin():
    return make_plugin()


@pytest.fixture
def valid_keys():
    return make_valid_keys()


@pytest.fixture
def parser():
    return {'errors': [], 'warnings': []}
