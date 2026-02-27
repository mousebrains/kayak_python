"""Flask application factory (replaces all of libHTML.a + libCMD.a)."""

from __future__ import annotations

from flask import Flask

from kayak.config import DEBUG, SECRET_KEY


def create_app(config: dict | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["DEBUG"] = DEBUG

    if config:
        app.config.update(config)

    # Register blueprints
    from kayak.web.routes.data_api import data_api_bp
    from kayak.web.routes.descriptions import descriptions_bp
    from kayak.web.routes.editing import editing_bp
    from kayak.web.routes.pages import pages_bp
    from kayak.web.routes.plots import plots_bp
    from kayak.web.routes.views import views_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(plots_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(descriptions_bp)
    app.register_blueprint(editing_bp)
    app.register_blueprint(data_api_bp)

    # Legacy CGI URL compatibility
    @app.route("/cgi/display")
    def legacy_cgi():
        """Redirect legacy CGI URLs to modern routes."""
        from flask import redirect, request, url_for

        if "M" in request.args or not request.args:
            return redirect(url_for("pages.page", name="main"))
        if "P" in request.args:
            return redirect(url_for("pages.page", name=request.args["P"]))
        if "F" in request.args:
            return redirect(url_for("pages.file_page", name=request.args["F"]))
        if "f" in request.args:
            return redirect(url_for("plots.flow_plot", key=request.args["f"]))
        if "g" in request.args:
            return redirect(url_for("plots.gage_plot", key=request.args["g"]))
        if "t" in request.args:
            return redirect(url_for("plots.temp_plot", key=request.args["t"]))
        if "v" in request.args:
            return redirect(url_for("views.view", key=request.args["v"]))
        if "e" in request.args:
            return redirect(url_for("editing.edit", key=request.args["e"]))
        if "D" in request.args:
            return redirect(url_for("descriptions.description", key=request.args["D"]))
        if "d" in request.args:
            return redirect(url_for("pages.page", name="d"))

        from flask import abort
        abort(404)

    return app
