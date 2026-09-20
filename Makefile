.PHONY: up down logs demo test

up:
	docker compose up --build -d

demo:
	SCANNER_BACKEND=replay docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f scanner api

test:
	PYTHONPATH=. python3 tests/test_parse.py
