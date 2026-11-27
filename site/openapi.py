# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write the server's OpenAPI document to openapi.json for the API reference.

Run from the repository root: uv run python site/openapi.py
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from swarmer.server.app import create_app

DESCRIPTION = """\
The API the Swarmer UI uses. Scripts authenticate with an API token, sent as a bearer token:

```sh
curl -H "Authorization: Bearer sw_..." \\
  https://swarmer.example.com/api/stacks
```

Create tokens under Account, API tokens. A token acts as the user who created it, limited to the
role picked for it. A running server serves this document at `/api/openapi.json` and an
interactive version at `/api/docs`.
"""


def short_operation_ids(spec: dict[str, Any]) -> None:
    """Drop the path and method from operation IDs, which become the reference page URLs.

    FastAPI builds IDs as the function name plus the path and method
    (`add_member_api_groups__group_id__members__user_id__put`). Names that more than one
    operation would share keep the full ID.
    """
    names = {}
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            suffix = re.sub(r"\W", "_", path) + "_" + method
            full = operation["operationId"]
            names[full] = full.removesuffix(suffix) if full.endswith(suffix) else full
    counts = Counter(names.values())
    for item in spec["paths"].values():
        for operation in item.values():
            name = names[operation["operationId"]]
            if name and counts[name] == 1:
                operation["operationId"] = name


def main() -> None:
    spec = create_app().openapi()
    short_operation_ids(spec)
    spec["info"]["description"] = DESCRIPTION
    spec["servers"] = [{"url": "https://swarmer.example.com"}]
    out = Path(__file__).parent / "openapi.json"
    out.write_text(json.dumps(spec, indent=2) + "\n")


if __name__ == "__main__":
    main()
