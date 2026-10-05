CREATE EXTENSION IF NOT EXISTS postgis;

-- Tree cover loss polygons (for example from a deforestation detection model or a public dataset).
CREATE TABLE IF NOT EXISTS forest_loss (
    id           bigserial PRIMARY KEY,
    source       text NOT NULL,
    detected_on  date NOT NULL,
    geom         geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS forest_loss_geom ON forest_loss USING gist (geom);
CREATE INDEX IF NOT EXISTS forest_loss_detected_on ON forest_loss (detected_on);

CREATE TABLE IF NOT EXISTS farm_list_reports (
    id          uuid PRIMARY KEY,
    customer    text NOT NULL,
    filename    text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    summary     jsonb NOT NULL,
    results     jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS farm_list_reports_customer ON farm_list_reports (customer, created_at DESC);
