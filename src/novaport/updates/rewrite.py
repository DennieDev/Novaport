# SPDX-License-Identifier: AGPL-3.0-or-later
"""Changes a service's image in a compose file without touching anything else in it."""

import yaml
from yaml.nodes import MappingNode, ScalarNode


class RewriteError(ValueError):
    pass


def _value(mapping: MappingNode, key: str) -> yaml.Node | None:
    for key_node, value_node in mapping.value:
        if isinstance(key_node, ScalarNode) and key_node.value == key:
            node: yaml.Node = value_node
            return node
    return None


def replace_image(compose: str, service: str, image: str) -> str:
    """Return the compose text with `services.<service>.image` set to `image`.

    Splices the new value over the old one using the parser's positions, so comments,
    ordering, and quoting elsewhere stay exactly as they were.
    """
    try:
        root = yaml.compose(compose)
    except yaml.YAMLError as exc:
        raise RewriteError(f"The compose file is not valid YAML: {exc}") from exc
    if not isinstance(root, MappingNode):
        raise RewriteError("The compose file is not a mapping.")
    services = _value(root, "services")
    if not isinstance(services, MappingNode):
        raise RewriteError("The compose file has no services.")
    node = _value(services, service)
    if not isinstance(node, MappingNode):
        raise RewriteError(f"Service {service!r} not found.")
    current = _value(node, "image")
    if not isinstance(current, ScalarNode):
        raise RewriteError(f"Service {service!r} has no image.")
    if current.style in ("|", ">"):
        raise RewriteError(f"The image of {service!r} is a block scalar; change it by hand.")

    if current.style == "'":
        replacement = "'" + image.replace("'", "''") + "'"
    elif current.style == '"':
        replacement = '"' + image.replace("\\", "\\\\").replace('"', '\\"') + '"'
    else:
        replacement = image
    start, end = current.start_mark.index, current.end_mark.index
    return compose[:start] + replacement + compose[end:]
