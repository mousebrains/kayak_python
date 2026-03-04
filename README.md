# Kayak

Real-time river level, flow, gage height, and temperature data aggregated from
government agencies (USGS, NOAA, USACE, USBR, IDWR) for the
[Willamette Kayak and Canoe Club](https://wkcc.org).

Live site: [levels.wkcc.org](https://levels.wkcc.org)

## Quick Start

```bash
pip install -e ".[dev]"
levels init-db       # Create schema, seed states/sources/fetch_urls
levels pipeline      # Fetch live data and generate HTML
```

## Details

See [CLAUDE.md](CLAUDE.md) for architecture, development setup, and conventions.
