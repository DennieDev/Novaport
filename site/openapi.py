# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write the server's OpenAPI document to openapi.json for the API reference.

Run from the repository root: uv run python site/openapi.py
"""

import json
from pathlib import Path

from swarmer.server.app import create_app

DESCRIPTION = """\
The API the Swarmer UI uses. Scripts authenticate with an API token, sent as a bearer token:

```sh
curl -H "Authorization: Bearer sw_..." https://swarmer.example.com/api/stacks
```

Create tokens under Account, API tokens. A token acts as the user who created it, limited to the
role picked for it. A running server serves this document at `/api/openapi.json` and an
interactive version at `/api/docs`.
"""


def main() -> None:
    spec = create_app().openapi()
    spec["info"]["description"] = DESCRIPTION
    spec["servers"] = [{"url": "https://swarmer.example.com"}]
    out = Path(__file__).parent / "openapi.json"
    out.write_text(json.dumps(spec, indent=2) + "\n")


if __name__ == "__main__":
    main()
