SHELL := /bin/sh

.PHONY: build run-dev test lint typecheck

build:
	docker build -t video-bot:latest .

run-dev:
	docker compose up --build

test:
	pytest -q

lint:
	ruff check .

format:
	ruff check --fix .

typecheck:
	mypy app