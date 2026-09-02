-- Mock of the two ADCON addVANTAGE tables the plugin reads, with 48 hours of
-- 10-minute samples for three demo stations, generated at container start so
-- the newest readings are always "now". Nothing here is real data.

CREATE TABLE node_60 (
    id           integer PRIMARY KEY,
    dtype        text NOT NULL,          -- 'DeviceNode' = station, 'AnalogTagNode' = parameter tag
    displayname  text,
    subclass     text,
    parent_id    integer,                -- tag -> its station
    latitude     double precision,
    longitude    double precision,
    timezoneid   text
);

CREATE TABLE historiandata (
    tag_id         integer NOT NULL,
    startdate      bigint  NOT NULL,     -- unix seconds
    enddate        bigint  NOT NULL,     -- unix seconds; the observation time
    measuringvalue double precision,
    status         integer NOT NULL DEFAULT 0   -- 0 = valid
);
CREATE INDEX historiandata_tag_start_idx ON historiandata (tag_id, startdate);

INSERT INTO node_60 (id, dtype, displayname, parent_id, latitude, longitude, timezoneid) VALUES
    (1001, 'DeviceNode', 'Kabete',  NULL, -1.25, 36.74, 'Africa/Nairobi'),
    (1002, 'DeviceNode', 'Lodwar',  NULL,  3.12, 35.60, 'Africa/Nairobi'),
    (1003, 'DeviceNode', 'Mombasa', NULL, -4.03, 39.62, 'Africa/Nairobi'),
    (1004, 'DeviceNode', 'Old logger (no coordinates)', NULL, NULL, NULL, NULL);

-- Six tags per station: id = station id * 10 + n
INSERT INTO node_60 (id, dtype, displayname, subclass, parent_id)
SELECT s.id * 10 + p.n, 'AnalogTagNode', p.name, p.subclass, s.id
FROM (VALUES (1001), (1002), (1003)) AS s(id),
     (VALUES (1, 'Air temperature',   'TEMPERATURE'),
             (2, 'Relative humidity', 'HUMIDITY'),
             (3, 'Air pressure',      'PRESSURE'),
             (4, 'Precipitation',     'PRECIPITATION'),
             (5, 'Wind speed',        'WINDSPEED'),
             (6, 'Wind direction',    'WINDDIRECTION')) AS p(n, name, subclass);

-- 10-minute samples for the last 48 hours; a smooth diurnal cycle plus noise
INSERT INTO historiandata (tag_id, startdate, enddate, measuringvalue, status)
SELECT t.id,
       extract(epoch FROM ts)::bigint - 600,
       extract(epoch FROM ts)::bigint,
       CASE t.subclass
           WHEN 'TEMPERATURE'   THEN round((21 + 6 * sin((extract(hour FROM ts) + extract(minute FROM ts) / 60 - 6) / 24 * 2 * pi()) + random() * 0.6 - 0.3)::numeric, 2)
           WHEN 'HUMIDITY'      THEN round((70 - 25 * sin((extract(hour FROM ts) + extract(minute FROM ts) / 60 - 6) / 24 * 2 * pi()) + random() * 4 - 2)::numeric, 1)
           WHEN 'PRESSURE'      THEN round((1012 + 2 * sin(extract(hour FROM ts) / 12 * pi()) + random() * 0.6 - 0.3)::numeric, 1)
           WHEN 'PRECIPITATION' THEN CASE WHEN extract(hour FROM ts) BETWEEN 11 AND 14 AND random() < 0.3 THEN round((random() * 1.2)::numeric, 1) ELSE 0 END
           WHEN 'WINDSPEED'     THEN round((2.5 + 2 * sin((extract(hour FROM ts) - 6) / 24 * 2 * pi()) + random() * 1.6 - 0.8)::numeric, 2)
           WHEN 'WINDDIRECTION' THEN round(((90 + 40 * sin((extract(hour FROM ts) - 6) / 24 * 2 * pi()) + random() * 30 - 15 + 360)::numeric) % 360, 0)
       END,
       0
FROM node_60 t,
     generate_series(date_trunc('hour', now()) - interval '48 hours', now(), interval '10 minutes') AS ts
WHERE t.dtype = 'AnalogTagNode';

-- A few rows the plugin must ignore: flagged invalid, and an hourly aggregate
INSERT INTO historiandata (tag_id, startdate, enddate, measuringvalue, status)
SELECT 10011, extract(epoch FROM ts)::bigint - 600, extract(epoch FROM ts)::bigint, -99, 1
FROM generate_series(date_trunc('hour', now()) - interval '3 hours', now(), interval '1 hour') AS ts;
INSERT INTO historiandata (tag_id, startdate, enddate, measuringvalue, status)
SELECT 10011, extract(epoch FROM ts)::bigint - 3600, extract(epoch FROM ts)::bigint, 21.5, 0
FROM generate_series(date_trunc('hour', now()) - interval '24 hours', now(), interval '1 hour') AS ts;

-- The read-only account ADL should be given
CREATE ROLE adl_reader LOGIN PASSWORD 'adl_reader';
GRANT CONNECT ON DATABASE addvantage TO adl_reader;
GRANT SELECT ON node_60, historiandata TO adl_reader;
