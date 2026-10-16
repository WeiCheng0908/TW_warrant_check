# FIX NOTE — handoff bug between frontend and backend

**Discovered by**: e2e verification (cycle 2)
**Fixed by**: owner (Mavis) after cycle 2 report

## Bug
- `templates/index.html` referenced static assets via Jinja: `{{ url_for('static', filename='style.css') }}`
- `app.py` was serving the index via `send_from_directory(TEMPLATES_DIR, "index.html")` which does NOT render Jinja
- Effect: home page would ship raw `{{ url_for(...) }}` tags to the browser; CSS/JS would not load.

## Fix
In `app.py`:
- Imported `render_template` from `flask`
- `index()` now uses `return render_template("index.html")` so Jinja renders properly

## How to verify
```bash
cd twse-warrant-finder
pip install -r requirements.txt
python app.py
# in another shell:
curl -s http://localhost:5000/ | head -10
# should see: <link rel="stylesheet" href="/static/style.css">
# NOT: <link rel="stylesheet" href="{{ url_for(...) }}">
```
