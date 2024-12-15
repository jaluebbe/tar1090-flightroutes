# tar1090-flightroutes

This repository provides an API to serve flight route information to tar1090.

## Objective

Given a large number of users and a large number of aircraft displayed, the computational effort should be minimized.

## Functionality

- **Requests**: tar1090 requests batches of up to 100 callsigns of aircraft with their current positions. In this approach, positional data validation will be ignored and considered optional.
- **Database**: The API obtains its flight route information from a Redis database. This Redis database is populated by several processes that are currently under development.
- **Data quality**: Depending on the quality and reliability of the data sources, entries might be labeled either as plausible or not.
