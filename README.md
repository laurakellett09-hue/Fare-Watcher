# Fare Watch

A personal flight-deal watcher from Melbourne. Every 2 hours it scores fares out of 100 and emails you any that score 80+. The phone app shows the deals and lets you change destinations, dates, and airlines.

- `index.html`: the app (hosted free on GitHub Pages)
- `fare_watch.py`: the checker (fetches, scores, emails)
- `config.json`: your settings (the app edits this for you)
- `.github/workflows/check-fares.yml`: runs the checker every 2 hours
- `data/`: results and price history, updated by the checker

Start with **SETUP.md**.
