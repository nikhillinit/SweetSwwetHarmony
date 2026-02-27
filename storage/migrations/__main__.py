"""Allow ``python -m storage.migrations`` to invoke the CLI."""

import asyncio

from storage.migrations.cli import main

if __name__ == "__main__":
    asyncio.run(main())
