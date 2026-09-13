# ParkSetu

Parking dashboard for malls and gated societies. Demo only.

Handles boom entry/exit, bay occupancy, FASTag vs cash, visitor passes, and a watchlist. All data is fake.

## Tech stack

- Python
- Flask
- SQLite
- HTML / CSS / JS

## Run

```
pip install -r requirements.txt
python seed.py
python app.py
```

Open http://127.0.0.1:5050

## Demo logins

| Role | Username | Password |
|---|---|---|
| HQ | admin | admin123 |
| Mall manager | kajal | kajal123 |
| Society manager | samantha | samantha123 |
| Car owner | nani | nani123 |

Run `python seed.py` again to reset the database.

## License

MIT — see `LICENSE`.
