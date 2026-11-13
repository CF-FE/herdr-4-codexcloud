"""Model admission for sessions started through Herdr."""

import re


DEFAULT_MODEL = "gpt-6.1-sol"
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
ASTRA = re.compile(r"(?<![a-z0-9])astra(?:[0-9]+)?(?![a-z0-9])")
GPT_MODEL = re.compile(r"gpt-[0-9]")
UNKNOWN_MODELS = {"", "unknown", "unconfirmed", "not_applicable"}


def model_name(model):
    name, separator, suffix = model.casefold().rsplit("/", 1)[-1].partition(":")
    if not separator:
        return name
    following = suffix.split(":", 1)[0]
    if ASTRA.search(following):
        return following
    # A colon can introduce a tag or a provider-qualified model.
    if (GPT_MODEL.match(name) or name in UNKNOWN_MODELS
            or (ASTRA.search(name) and not GPT_MODEL.match(following))):
        return name
    return following


def is_astra_model(model):
    return ASTRA.search(model_name(model)) is not None


def require_session_model(model):
    if (not isinstance(model, str) or not MODEL_ID.fullmatch(model)
            or model_name(model) in UNKNOWN_MODELS):
        raise ValueError("An explicit Herdr model identity is required")
    if is_astra_model(model):
        raise ValueError("Astra models are prohibited for Herdr sessions")


def codex_option(arg):
    key, separator, value = arg.partition("=")
    if key[:2] in {"-m", "-c"} and len(key) > 2:
        return key[:2], "=", arg[2:]
    return key, separator, value


def codex_model_override(key, value):
    """Check an explicit CLI model override; return the root model if present."""
    if key in {"-c", "--config"}:
        config_key, _, value = value.partition("=")
        config_key = config_key.strip()
        if config_key.rsplit(".", 1)[-1] != "model":
            return None
        value = value.strip()
        if len(value) > 1 and value[0] in "'\"" and value[-1] == value[0]:
            value = value[1:-1]
        require_session_model(value)
        return value if config_key == "model" else None
    if key in {"-m", "--model"}:
        require_session_model(value)
        return value
    return None


def require_resume_model(args):
    """Accept a frozen command only when its direct model choice is explicit."""
    explicit = set()
    options = iter(args)
    for arg in options:
        if arg == "--":
            raise ValueError("RESUME_MODEL_OPTIONS_INVALID")
        key, separator, value = codex_option(arg)
        if key in {"-m", "--model", "-c", "--config"}:
            if not separator:
                value = next(options, "")
                if not value or value.startswith("-"):
                    raise ValueError("RESUME_MODEL_OPTIONS_INVALID")
            model = codex_model_override(key, value)
            if model is not None:
                explicit.add(model)
    if not explicit:
        raise ValueError("RESUME_MODEL_REQUIRED")
    if len(explicit) != 1:
        raise ValueError("RESUME_MODEL_AMBIGUOUS")
