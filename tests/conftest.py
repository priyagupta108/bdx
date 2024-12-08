from pathlib import Path

import pytest

# isort: off
from bdx.index import (
    IndexingOptions,
    SymbolIndex,
    index_binary_directory,
)

# isort: on


FIXTURE_PATH = Path(__file__).parent / "fixture"


@pytest.fixture(scope="session")
def readonly_index(tmp_path_factory):
    index_path = tmp_path_factory.mktemp("index")
    index_binary_directory(FIXTURE_PATH, index_path, IndexingOptions())
    with SymbolIndex.open(index_path, readonly=True) as index:
        yield index


@pytest.fixture(scope="session")
def fixture_path():
    return FIXTURE_PATH
