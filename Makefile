PYTHON ?= python3

black:
	${PYTHON} -m black bdx/ tests/

isort:
	${PYTHON} -m isort -i bdx/*.py tests/*.py

format: black isort

mypy:
	${PYTHON} -m mypy bdx

ruff:
	${PYTHON} -m ruff check bdx/

lint: ruff mypy

pytest:
	${PYTHON} -m pytest

check: pytest

.PHONY: black isort format mypy ruff lint pytest check
