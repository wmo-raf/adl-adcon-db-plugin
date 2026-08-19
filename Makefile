# This repo's stack is defined in docker-compose.dev.yml rather than the
# fleet's docker-compose.yml, so the file has to be named explicitly.
test:
	docker compose -f docker-compose.dev.yml exec adl adl test --keepdb adl_adcon_db_plugin.tests
