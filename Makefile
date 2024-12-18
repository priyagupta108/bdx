PYTHON ?= python3
PYTEST_ARGS ?= '-v'

black:
	${PYTHON} -m black bdx/ tests/

black-check:
	${PYTHON} -m black --check bdx/ tests/

isort:
	${PYTHON} -m isort bdx/*.py tests/*.py

isort-check:
	${PYTHON} -m isort --check bdx/*.py tests/*.py

format: black isort

checkformat: black-check isort-check

mypy:
	${PYTHON} -m mypy bdx

ruff:
	${PYTHON} -m ruff check bdx/

lint: ruff mypy

pytest:
	${PYTHON} -m pytest ${PYTEST_ARGS}

check: pytest

.PHONY: black black-check isort isort-check format checkformat mypy ruff lint pytest check
