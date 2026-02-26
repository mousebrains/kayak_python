# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kayak is a C++ web application that aggregates real-time river level, flow, gage, and temperature data from multiple government agencies (USGS, NOAA, USACE, USBR, IDWR, etc.) into a MySQL database and serves it via CGI on Apache. The site was hosted at levels.wkcc.org.

## Build Commands

```bash
make              # Build all programs and CGI binaries (delegates to src/ and scripts/)
make clean        # Remove binaries and object files
make distclean    # Same as clean
make install      # Install binaries to /home/tpw/local/bin and web assets to ~/public_html
```

The src/Makefile is the main build file. It compiles C++20 with g++ and links against mysql++, libcurl, libxml2, freetype, libgif, libpng, and zlib.

Object files go into `src/obj/`. Source files use `.C` for implementation and `.H` for headers.

## Architecture

### Data Pipeline

The `scripts/master` script runs the full pipeline in order:

1. **mkMainPage** — generates the main levels HTML page
2. **mkDescription** — generates per-river description pages
3. **fetcher** — fetches data from remote agencies, parses it, stores in MySQL
4. **calcRating** — applies rating tables (gage height → flow conversions)
5. **merger** — merges data from multiple sources into combined tables
6. **calculator** — builds synthetic/calculated gage readings
7. **builder** — generates per-state HTML/CSV/text output pages

### MySQL Databases

Three databases accessed via the mysql++ library (`MyDB` wrapper class):
- **levels_information** (`InfoDB`) — master table of river/gage metadata, URL sources, parameters
- **levels_data** (`DataDB`) — time-series data tables for flow/gage/temperature per station
- **levels_page** (`PageDB`) — pre-rendered page cache for the web frontend

Connection config is hardcoded in `src/Paths.C`.

### Data Types

`DataDB::TYPE` enum: `FLOW`, `INFLOW`, `OUTFLOW`, `GAGE`, `TEMPERATURE`. Table names are derived from a station key + type suffix (e.g., `StationName.flow`).

### Parsers (`src/Parse*.C`)

Each data source has a dedicated parser inheriting from `Parsers::Parse` (abstract base in `Parse.H`). The virtual `line()` method processes one line of fetched text. Parser types are selected by string name in `fetcher.C::makeParser()`. Parsers include: USGS, NOAA, NWRFC, USBR, USACE, CBRFC, OCS, IDWR, IdahoPower, Wa_Gov, and others.

### CGI Programs

CGI binaries (installed to `~/public_html/cgi/`):
- **display** — main entry point; dispatches on query params: `P=` page, `F=` file, `f=/g=/t=` flow/gage/temp plot, `v=` view, `e=` edit, `D=` description
- **svg/png** — generate time-series plots using the Canvas/Plot abstraction
- **data** — serves raw data
- **submit/approve/authenticate** — data editing workflow
- **makePage/picker/printenv** — utility CGI scripts

### Static Libraries (built in src/obj/)

- **libKayak.a** — core: DB wrappers, file I/O, string utils, URL parsing, display logic, XML, timezone, GIF
- **libHTML.a** — HTTP/CGI response helpers, URL encode/decode, HTML compression
- **libParse.a** — all data source parsers
- **libCMD.a** — CGI command dispatch (description, page, file, plot, edit, view)
- **libPlot.a** — plotting engine: Canvas (abstract) → SVGCanvas, PNGCanvas, BitMapCanvas; axes, transforms, strokes

### SQL Database Setup

`gen.sql/rebuild` regenerates all SQL databases from scratch by running the scripts in `gen.sql/` (setup, mkParameters, mkURLs, mkDescription, mkFlows, mkMaster). The URL fetch list in `files/Makefile.fetch` is auto-generated from URL definitions in `files/url/`.

## Conventions

- Source files use `.C`/`.H` extensions (not `.cpp`/`.hpp`)
- Include guards use `#ifndef INC_ClassName_H_` pattern
- Classes follow a `ClassName.C` / `ClassName.H` naming convention; CGI main programs are lowercase
- Build uses implicit Make rules with `obj/%.o: %.C` pattern rule
- getopt-style CLI argument parsing in command-line tools
