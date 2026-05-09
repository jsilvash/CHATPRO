.PHONY: dev test migrate shell format lint

dev:
	docker-compose up

test:
	docker-compose run --rm web \
	  sh -c "DATABASE_URL=postgresql://chatpro:chatpro@db:5432/chatpro_test pytest -v"

migrate:
	docker-compose run --rm --no-deps web alembic upgrade head

shell:
	docker-compose run --rm web python

format:
	ruff format src tests

lint:
	ruff check src tests
