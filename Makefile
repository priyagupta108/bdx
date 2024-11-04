PYTHON ?= python3

black:
	${PYTHON} -m black bdx/ tests/

black-check:
	${PYTHON} -m black --check bdx/ tests/

isort:
	${PYTHON} -m isort -i bdx/*.py tests/*.py

isort-check:
	${PYTHON} -m isort --check -i bdx/*.py tests/*.py

format: black isort

checkformat: black-check isort-check

mypy:
	${PYTHON} -m mypy bdx

ruff:
	${PYTHON} -m ruff check bdx/

lint: ruff mypy

pytest:
	${PYTHON} -m pytest

check: pytest

.PHONY: black black-check isort isort-check format checkformat mypy ruff lint pytest check
