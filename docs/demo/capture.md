# Capturing the Watch-mode GIF

The supporting GIF shows the sandbox query editor in **Watch** mode: publish a change, watch the subscribed context update itself.

## Steps
1. Start the stack: `docker compose up` (images build `--platform linux/amd64`).
2. Publish a couple of items so there's something to match, e.g.:
   ```bash
   curl -X POST http://localhost:8000/api/v1/data/publish -H "Content-Type: application/json" \
     -d '{"project_id":"quickstart","data_key":"pg_dsn","data":{"engine":"postgres","host":"db.internal","port":5432}}'
   ```
3. Open `http://localhost:8000/sandbox`, select the `quickstart` project, type a need such as
   *"how the service reaches its datastore"*, and click **Watch**. The results pane populates.
4. Start a screen recorder scoped to the browser window.
5. In another terminal, publish a change to `pg_dsn` (new `host`/`port`). Record the matching
   card in the results pane flashing and showing the new value — no refresh.
6. Stop recording; convert to GIF, e.g.
   `ffmpeg -i demo.mov -vf "fps=12,scale=960:-1" docs/assets/demo.gif`.
7. Keep it short (5-8s) and under ~3 MB so it renders inline on GitHub.

Save the result as `docs/assets/demo.gif`.
