# Database Schema

Entity-relationship diagram for the `kayak` database (18 tables).

**Relationship legend:**
- `||--||` — one to one
- `||--o{` — one to many
- `}o--o{` — many to many (via junction table)

```mermaid
erDiagram

    gauge {
        int id PK
        string name UK
        float bank_full
        float flood_stage
        text location
        decimal latitude
        decimal longitude
        float elevation
        string station_id
        string cbtt_id
        string geos_id
        string nws_id
        string nwsli_id
        string snotel_id
        string usgs_id
        int rating_id FK
    }

    source {
        int id PK
        string name
        string agency
        int fetch_url_id FK
        int calc_expression_id FK
    }

    gauge_source {
        int gauge_id FK,PK
        int source_id FK,PK
    }

    fetch_url {
        int id PK
        string url UK
        string parser
        string hours
        bool is_active
        datetime last_fetched_at
    }

    calc_expression {
        int id PK
        enum data_type
        string expression
        text time_expression
        text note
    }

    rating {
        int id PK
        string url
        string parser
    }

    rating_data {
        int rating_id FK,PK
        float gauge_height_ft PK
        float flow_cfs
    }

    observation {
        int source_id FK,PK
        datetime observed_at PK
        enum data_type PK
        float value
    }

    latest_observation {
        int source_id FK,PK
        enum data_type PK
        datetime observed_at
        float value
        datetime prev_observed_at
        float prev_value
        float delta_per_hour
    }

    reach {
        int id PK
        datetime updated_at
        int gauge_id FK
        string name UK
        text display_name
        string sort_name
        text description
        text difficulties
        text basin
        float basin_area
        float elevation
        float elevation_lost
        float length
        float gradient
        float max_gradient
        decimal latitude
        decimal longitude
        decimal latitude_start
        decimal longitude_start
        decimal latitude_end
        decimal longitude_end
        int aw_id
        bool no_show
    }

    state {
        int id PK
        string name UK
        string abbreviation
    }

    reach_state {
        int reach_id FK,PK
        int state_id FK,PK
    }

    reach_class {
        int id PK
        int reach_id FK
        string name
        float low
        enum low_data_type
        float high
        enum high_data_type
    }

    reach_level {
        int id PK
        int reach_id FK
        enum level
        float low
        enum low_data_type
        float high
        enum high_data_type
    }

    class_description {
        string name PK
        text description
    }

    guidebook {
        int id PK
        string title
        string subtitle
        string edition
        text author
        text url
    }

    reach_guidebook {
        int reach_id FK,PK
        int guidebook_id FK,PK
        text page
        text run
        text url
    }

    pages {
        string name PK
        enum action
        int expires
        datetime modified
        text mimetype
        text body
    }

    %% ── One-to-many relationships ──

    rating ||--o{ gauge : "has"
    rating ||--o{ rating_data : "has"

    fetch_url ||--o{ source : "provides"
    calc_expression ||--|| source : "defines"

    source ||--o{ observation : "records"
    source ||--o{ latest_observation : "caches"

    gauge ||--o{ reach : "measures"

    reach ||--o{ reach_class : "classified by"
    reach ||--o{ reach_level : "leveled by"

    %% ── Many-to-many relationships (via junction tables) ──

    gauge }o--o{ source : "gauge_source"
    reach }o--o{ state : "reach_state"
    reach }o--o{ guidebook : "reach_guidebook"
```

## Table Counts by Domain

| Domain | Tables |
|---|---|
| **Gauges & Sources** | `gauge`, `source`, `gauge_source`, `fetch_url`, `calc_expression` |
| **Observations** | `observation`, `latest_observation` |
| **Ratings** | `rating`, `rating_data` |
| **Reaches** | `reach`, `reach_class`, `reach_level`, `reach_state` |
| **Reference** | `state`, `class_description`, `guidebook`, `reach_guidebook` |
| **Cache** | `pages` |
