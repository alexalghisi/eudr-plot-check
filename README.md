# eudr-plot-check

Under the EU Deforestation Regulation (EUDR), every lot of coffee, cocoa and other covered commodities has to be traceable to the plots of land it was grown on, with proof that no deforestation happened there after 31 December 2020. In practice that means customers send in farm lists: spreadsheets and GeoJSON exports from cooperatives, of very mixed quality.

This service reads those lists, finds what's wrong with each plot in a way a supplier can act on, and checks every usable plot against tree cover loss data in PostGIS.

![Checking an example farm list from the CLI and the API, then the plots on a map](docs/preview.gif)

## What it checks

| Check | Severity | Why |
| --- | --- | --- |
| Coordinates with fewer than 6 decimal digits | error | EUDR requires geolocation precise to six decimals |
| Latitude and longitude swapped (points and polygons) | error | Very common in exports. Detected because the swapped location falls inside the declared country |
| Location outside the declared country | error | A mistyped country or wrong coordinates |
| A point for a plot over 4 ha | error | Larger plots must be described by a polygon |
| Unreadable geometry, or a polygon that can't be repaired | error | |
| Duplicate `farm_id` | error | |
| Self-intersecting polygon | warning | Repaired with `ST_MakeValid`, and the report says so |
| Unclosed polygon ring | warning | Closed automatically |
| Declared area differs from the measured area by more than 20% | warning | Area is measured geodesically (`ST_Area` on geography), not in degrees |
| Plots overlapping by more than 10% of the smaller one | warning | Often the same plot twice, or a digitising error |
| Tree cover loss after the cutoff intersecting the plot | `deforestation_risk` | Points are assessed as a circle of their declared area, or of 4 ha when none is declared. Overlaps under 100 m² are treated as edge noise |

**Every problem is reported.** Each row gets all of its problems, with its spreadsheet row number and the field to fix, rather than failing on the first one. Plots with errors are `needs_fix`. Plots with only warnings stay `ok`, so usable data isn't blocked.

**What a customer gets back:**

- **A summary.**
- **A fix list.** `issues.csv` has one line per problem, ready to send back to the supplier.
- **A map file.** `plots.geojson` holds every assessed geometry with its status and issues, to open in QGIS or any web map.

![Plots coloured by status, with tree cover loss before and after the cutoff](docs/map.png)

## Design

- **PostGIS does the geometry.** Each list is loaded into a temporary table, and validity, repair, geodesic area, overlaps (a self-join with `ST_Intersects`) and the deforestation intersection (`ST_Union` of loss polygons per plot, so overlapping loss polygons aren't double counted) all run in SQL against GiST-indexed data.
- **Python handles the rest.** It parses files, applies rules that need context (precision, country bounds, the 4 ha rule) and turns results into messages people understand.
- **Customers only see their own reports.** API keys map to customers, and another customer's report id returns 404.
- **Uploads have limits.** Files are capped at 10 MB and 50,000 rows, and a file without the required columns gets a 422 that names the columns it found.

Country checks use approximate bounding boxes for the main producer countries. That is enough to catch swapped coordinates and wrong countries. A production deployment would load real boundaries into PostGIS and use `ST_Contains`.

## Tech stack

Python 3.12, PostgreSQL 16 and PostGIS 3.4, psycopg 3, FastAPI, Pydantic, Docker, Terraform (AWS ECS Fargate, RDS, Secrets Manager), pytest, ruff, mypy (strict)

## Running it

```bash
docker compose up -d db
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
export DATABASE_URL=postgresql://plots:plots@localhost/plots

plot-check load-forest-loss examples/forest_loss.geojson --source "example layer"
plot-check check examples/farms.csv            # exit code 1 if anything needs fixing or shows deforestation
```

The API:

```bash
PLOT_CHECK_API_KEYS=dev-key:demo-roaster uvicorn plot_check.api:app_from_env --factory
curl -H "Authorization: Bearer dev-key" -F file=@examples/farms.csv localhost:8000/farm-lists
curl -H "Authorization: Bearer dev-key" localhost:8000/farm-lists/<id>/issues.csv
curl -H "Authorization: Bearer dev-key" localhost:8000/farm-lists/<id>/plots.geojson
```

Farm lists are CSV, with `farm_id`, `country` (ISO alpha-2), `latitude`/`longitude` or a WKT `polygon` in lon lat order, and an optional `area_ha`. GeoJSON FeatureCollections are also accepted, including the `ProducerName`, `ProducerCountry` and `Area` property names used in EU information system exports.

Forest loss layers are GeoJSON polygons with a `detected_on` date.

## Deploying

`infra/` is Terraform for AWS:

- RDS PostgreSQL 16, encrypted, with backups (PostGIS is enabled by the app's migration);
- the API on ECS Fargate behind an existing load balancer, with a deployment circuit breaker;
- secrets in Secrets Manager;
- logs in CloudWatch;
- an ECR repository with scan on push.

```bash
cd infra && terraform init && terraform plan -var environment=staging -var image_tag=… # plus network inputs
```

## Tests

```bash
ruff check . && ruff format --check . && mypy
PLOT_CHECK_TEST_DATABASE_URL=postgresql://user:pass@localhost/plots_test pytest   # needs PostGIS
cd infra && terraform fmt -check && terraform validate
```

## License

MIT
