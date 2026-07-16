# Team Wins

Select your favorite team from each major sport (NFL, NBA, MLB, NHL) and see how good your month has been.

## Data

`scrape_scores.py` pulls the previous day's final scores from ESPN and appends
them to per-league CSVs under `data/` (see `PLAN.md` for the full design).

## Web app

A small stdlib-only web app reads those CSVs and shows how many wins your
picked teams have across a chosen month.

```
python app.py            # serve on http://localhost:8000
python app.py --port 5000
```

Then open the URL, pick one team per league, and choose a month. Your picks
default to `my_teams.json`. The win math lives in `win_index.py` (pure
functions over the CSV rows) and is covered by `tests/test_win_index.py`.

## Development

```
pip install -r requirements.txt
pip install pytest
pytest
```
