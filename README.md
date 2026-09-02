# ADL ADCON DB Plugin

Collects observation data from an **ADCON Telemetry addVANTAGE Pro** server by
reading its PostgreSQL database directly, into an
[ADL](https://github.com/wmo-raf/adl) instance. On each collection cycle ADL
queries the historian table for the parameter tags you have mapped and stores
the samples against your ADL stations and data parameters.

**Operator guide:** [docs/guide.md](docs/guide.md) — prerequisites (database
access and account), installation, every connection and station-link field,
the station/tag selectors, collection behaviour, diagnostics and
troubleshooting. The guide is also published on the central ADL documentation
site.

## Development setup

The plugin runs inside the ADL core image. Build the `adl:latest` image from
the [ADL core repository](https://github.com/wmo-raf/adl) first, then:

```bash
git clone https://github.com/wmo-raf/adl-adcon-db-plugin.git
cd adl-adcon-db-plugin
cp .env.sample .env        # set PLUGIN_BUILD_UID=$(id -u), PLUGIN_BUILD_GID=$(id -g), ADL_DB_PASSWORD
docker compose -f docker-compose.dev.yml build
docker compose -f docker-compose.dev.yml up
docker compose -f docker-compose.dev.yml exec adl adl createsuperuser
```

The admin is served by the bundled nginx proxy on `ADL_WEB_PROXY_PORT`
(default 80). The plugin source is bind-mounted, so code changes reload the
dev server. If the build fails with `pull access denied` for `adl:latest`,
prefix the build with `DOCKER_BUILDKIT=0`.

A mock addVANTAGE database for local testing is in
`docs/screenshots/compose.mock.yml` (used by the documentation screenshot
harness; see [CONTRIBUTING.md](CONTRIBUTING.md)). Lint and format from
`plugins/adl_adcon_db_plugin/` with `make lint` and `make format`. A change to
any connection or station-link field must update the guide in the same PR.
