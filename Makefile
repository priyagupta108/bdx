PYTHON ?= python3

black:
	${PYTHON} -m black bdx/

isort:
	${PYTHON} -m isort -i bdx/*.py

format: black isort

mypy:
	${PYTHON} -m mypy bdx

ruff:
	${PYTHON} -m ruff check bdx/

lint: ruff mypy

.PHONY: black isort format mypy ruff lint
