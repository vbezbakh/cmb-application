CREATE TABLE items (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- The provider's event id is the primary key: that is what makes webhook handling idempotent.
CREATE TABLE payment_events (
    event_id    text        PRIMARY KEY,
    event_type  text        NOT NULL,
    payload     jsonb       NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX payment_events_received_at_idx ON payment_events (received_at);

CREATE TABLE daily_payment_summary (
    day         date        NOT NULL,
    event_type  text        NOT NULL,
    event_count bigint      NOT NULL,
    computed_at timestamptz NOT NULL,
    PRIMARY KEY (day, event_type)
);

CREATE TABLE batch_runs (
    run_date     date        PRIMARY KEY,
    rows_written integer     NOT NULL,
    finished_at  timestamptz NOT NULL
);
