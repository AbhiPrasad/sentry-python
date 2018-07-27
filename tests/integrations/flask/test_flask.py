import json
import pytest

from io import BytesIO

flask = pytest.importorskip("flask")

from flask import Flask, request

from flask_login import LoginManager, login_user

from sentry_sdk import capture_message, capture_exception
from sentry_sdk.integrations.logging import LoggingIntegration
import sentry_sdk.integrations.flask as flask_sentry


login_manager = LoginManager()


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "haha"

    login_manager.init_app(app)

    @app.route("/message")
    def hi():
        capture_message("hi")
        return "ok"

    return app


def test_has_context(sentry_init, app, capture_events):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])
    events = capture_events()

    client = app.test_client()
    response = client.get("/message")
    assert response.status_code == 200

    event, = events
    assert event["transaction"] == "hi"
    assert "data" not in event["request"]
    assert event["request"]["url"] == "http://localhost/message"


@pytest.mark.parametrize("debug", (True, False))
@pytest.mark.parametrize("testing", (True, False))
def test_errors(sentry_init, capture_exceptions, app, debug, testing):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    app.debug = debug
    app.testing = testing

    @app.route("/")
    def index():
        1 / 0

    exceptions = capture_exceptions()

    client = app.test_client()
    try:
        client.get("/")
    except ZeroDivisionError:
        pass

    exc, = exceptions
    assert isinstance(exc, ZeroDivisionError)


def test_flask_login_not_installed(sentry_init, app, capture_events, monkeypatch):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    monkeypatch.setattr(flask_sentry, "current_user", None)

    events = capture_events()

    client = app.test_client()
    client.get("/message")

    event, = events
    assert event.get("user", {}).get("id") is None


def test_flask_login_not_configured(sentry_init, app, capture_events, monkeypatch):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    assert flask_sentry.current_user is not None

    events = capture_events()
    client = app.test_client()
    client.get("/message")

    event, = events
    assert event.get("user", {}).get("id") is None


def test_flask_login_partially_configured(
    sentry_init, app, capture_events, monkeypatch
):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    events = capture_events()

    login_manager = LoginManager()
    login_manager.init_app(app)

    client = app.test_client()
    client.get("/message")

    event, = events
    assert event.get("user", {}).get("id") is None


@pytest.mark.parametrize("user_id", [None, "42", 3])
def test_flask_login_configured(sentry_init, app, user_id, capture_events, monkeypatch):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    class User(object):
        is_authenticated = is_active = True
        is_anonymous = user_id is not None

        def get_id(self):
            return str(user_id)

    @login_manager.user_loader
    def load_user(user_id):
        if user_id is not None:
            return User()

    @app.route("/login")
    def login():
        if user_id is not None:
            login_user(User())
        return "ok"

    events = capture_events()

    client = app.test_client()
    assert client.get("/login").status_code == 200
    assert not events

    assert client.get("/message").status_code == 200

    event, = events
    if user_id is None:
        assert event.get("user", {}).get("id") is None
    else:
        assert event["user"]["id"] == str(user_id)


def test_flask_large_json_request(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    data = {"foo": {"bar": "a" * 2000}}

    @app.route("/", methods=["POST"])
    def index():
        assert request.json == data
        assert request.data == json.dumps(data).encode("ascii")
        assert not request.form
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", content_type="application/json", data=json.dumps(data))
    assert response.status_code == 200

    event, = events
    assert event[""]["request"]["data"]["foo"]["bar"] == {
        "": {"len": 2000, "rem": [["!len", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]["bar"]) == 512
    assert event["request"]["data_info"] == {"ct": "json", "repr": "structured"}


def test_flask_large_formdata_request(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    data = {"foo": "a" * 2000}

    @app.route("/", methods=["POST"])
    def index():
        assert request.form["foo"] == data["foo"]
        assert not request.data
        assert not request.json
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    event, = events
    assert event[""]["request"]["data"]["foo"] == {
        "": {"len": 2000, "rem": [["!len", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]) == 512
    assert event["request"]["data_info"] == {"ct": "urlencoded", "repr": "structured"}


@pytest.mark.parametrize("input_char", [u"a", b"a"])
def test_flask_large_text_request(sentry_init, input_char, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    data = input_char * 2000

    @app.route("/", methods=["POST"])
    def index():
        assert not request.form
        if isinstance(data, bytes):
            assert request.data == data
        else:
            assert request.data == data.encode("ascii")
        assert not request.json
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    event, = events
    assert event[""]["request"]["data"] == {
        "": {"len": 2000, "rem": [["!len", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]) == 512
    assert event["request"]["data_info"] == {"ct": "plain", "repr": "other"}


def test_flask_large_bytes_request(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    data = b"\xc3" * 2000

    @app.route("/", methods=["POST"])
    def index():
        assert not request.form
        assert request.data == data
        assert not request.json
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    event, = events
    assert event[""]["request"]["data"] == {
        "": {"len": 2668, "rem": [["!len", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]) == 512
    assert event["request"]["data_info"] == {"ct": "bytes", "repr": "base64"}


def test_flask_files_and_form(sentry_init, capture_events, app):
    sentry_init(integrations=[flask_sentry.FlaskIntegration()])

    data = {"foo": "a" * 2000, "file": (BytesIO(b"hello"), "hello.txt")}

    @app.route("/", methods=["POST"])
    def index():
        assert list(request.form) == ["foo"]
        assert list(request.files) == ["file"]
        assert not request.json
        capture_message("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    response = client.post("/", data=data)
    assert response.status_code == 200

    event, = events
    assert event[""]["request"]["data"]["foo"] == {
        "": {"len": 2000, "rem": [["!len", "x", 509, 512]]}
    }
    assert len(event["request"]["data"]["foo"]) == 512

    assert event[""]["request"]["data"]["file"] == {
        "": {"len": 0, "rem": [["!filecontent", "x", 0, 0]]}
    }
    assert not event["request"]["data"]["file"]


@pytest.mark.parametrize(
    "integrations",
    [
        [flask_sentry.FlaskIntegration()],
        [flask_sentry.FlaskIntegration(), LoggingIntegration(event_level="ERROR")],
    ],
)
def test_errors_not_reported_twice(sentry_init, integrations, capture_events, app):
    sentry_init(integrations=integrations)

    @app.route("/")
    def index():
        try:
            1 / 0
        except Exception as e:
            app.logger.exception(e)
            raise e

    events = capture_events()

    client = app.test_client()
    with pytest.raises(ZeroDivisionError):
        client.get("/")

    assert len(events) == 1


def test_logging(sentry_init, capture_events, app):
    # ensure that Flask's logger magic doesn't break ours
    sentry_init(
        integrations=[
            flask_sentry.FlaskIntegration(),
            LoggingIntegration(event_level="ERROR"),
        ]
    )

    @app.route("/")
    def index():
        app.logger.error("hi")
        return "ok"

    events = capture_events()

    client = app.test_client()
    client.get("/")

    event, = events
    assert event["level"] == "error"


def test_no_errors_without_request(app):
    with app.app_context():
        capture_exception(ValueError())