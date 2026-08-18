# Capturing the sandbox demo GIF

The README GIF shows the core loop: publish a change, watch the subscribed context update itself live.

## Steps
1. Start the stack (Postgres + Redis + app):
   `docker compose up --build` (images build with `--platform linux/amd64`).
2. Open `http://localhost:8000/sandbox/demo`. The context panel populates from the pre-seeded
   `sandbox-demo` project against the need "database connection settings".
3. Start a screen recorder scoped to the browser window (e.g. macOS `Cmd-Shift-5`, or `peek` on Linux).
4. In the right pane, edit the `db_config` JSON (change `host`/`port`) and click **Publish**.
5. Record the `db_config` card in the left pane flashing and showing the new value — no page refresh.
6. Stop recording; export/convert to GIF (e.g. `ffmpeg -i demo.mov -vf "fps=12,scale=960:-1" docs/assets/demo.gif`).
7. Keep it short (5-8s) and under ~3 MB so it renders inline on GitHub.

Save the result as `docs/assets/demo.gif`.
