"""``${ENV_VAR}`` substitution in connection fields.

``${NAME}`` is replaced by the environment variable, ``${NAME:-fallback}`` uses the fallback when it
is unset or empty, and ``$${`` produces a literal ``${``. Substitution happens at connect time, so
the saved config keeps the placeholders. Only string fields are expanded (not the port).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from .models import FileConnection, ServerConnection, SshConfig, SshHop, replace

_PATTERN = re.compile(r"\$\$\{|\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class UnresolvedVariableError(ValueError):
    """One or more ``${NAME}`` placeholders have no value and no fallback."""

    def __init__(self, names: list[str]) -> None:
        self.names = tuple(dict.fromkeys(names))
        super().__init__("undefined environment variable(s): " + ", ".join(self.names))


def has_placeholders(value: str) -> bool:
    return any(match.group(1) for match in _PATTERN.finditer(value))


def expand(value: str, environ: Mapping[str, str], missing: list[str] | None = None) -> str:
    """Expand placeholders in ``value``; undefined names go to ``missing`` or raise."""
    unresolved: list[str] = [] if missing is None else missing

    def substitute(match: re.Match[str]) -> str:
        if match.group(0) == "$${":
            return "${"
        name, fallback = match.group(1), match.group(2)
        found = environ.get(name)
        if found:
            return found
        if fallback is not None:
            return fallback
        unresolved.append(name)
        return match.group(0)

    result = _PATTERN.sub(substitute, value)
    if missing is None and unresolved:
        raise UnresolvedVariableError(unresolved)
    return result


def _expand_ssh(
    ssh: SshConfig | None, environ: Mapping[str, str], missing: list[str]
) -> dict[str, object] | None:
    if ssh is None:
        return None

    def hop(value: SshHop) -> dict[str, object]:
        return {
            **value.model_dump(),
            "host": expand(value.host, environ, missing),
            "user": expand(value.user, environ, missing),
            "key_file": expand(value.key_file, environ, missing),
        }

    return {"server": hop(ssh.server), "jump": hop(ssh.jump) if ssh.jump else None}


def resolve_config(
    config: ServerConnection | FileConnection,
    password: str | None,
    environ: Mapping[str, str],
) -> tuple[ServerConnection | FileConnection, str | None]:
    """Expand placeholders in every string field and in ``password``; raises if any is undefined."""
    missing: list[str] = []
    if isinstance(config, ServerConnection):
        changes: dict[str, object] = {
            "host": expand(config.host, environ, missing),
            "user": expand(config.user, environ, missing),
            "database": expand(config.database, environ, missing),
            "service": expand(config.service, environ, missing),
            "options": {k: expand(v, environ, missing) for k, v in config.options.items()},
            "ssl": {
                **config.ssl.model_dump(),
                "ca_file": expand(config.ssl.ca_file, environ, missing),
                "cert_file": expand(config.ssl.cert_file, environ, missing),
                "key_file": expand(config.ssl.key_file, environ, missing),
            },
            "ssh": _expand_ssh(config.ssh, environ, missing),
        }
    else:
        changes = {"path": expand(config.path, environ, missing)}
    resolved_password = expand(password, environ, missing) if password is not None else None
    if missing:
        raise UnresolvedVariableError(missing)
    return replace(config, **changes), resolved_password
