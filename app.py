"""
Sluiskade — minimal placeholder app.

This is the very first iteration: just enough to prove the deployment
pipeline (GitHub → Coolify → Traefik → DNS → SSL) works end to end.
Real routes get added in subsequent sprints.
"""
import os
from flask import Flask, render_template

app = Flask(__name__)

# Read SECRET_KEY from env in production; fall back to a dev-only value
# locally so the app boots without a .env file present.
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "dev-secret-do-not-use-in-production",
)


@app.route("/")
def index():
    """Public splash — community one-pager comes in Sprint 2."""
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    """Healthcheck endpoint — used by Docker HEALTHCHECK + Uptime Kuma."""
    return {"status": "ok"}, 200


if __name__ == "__main__":
    # Local dev only; production runs under gunicorn (see Dockerfile CMD)
    app.run(host="0.0.0.0", port=5000, debug=True)
