"""Seed the writable account pool once; subsequent deployments preserve API edits."""

import os
import tempfile
from pathlib import Path

from services.electricity import ElectricityAccountPool


def seed_account_pool(source: Path, destination: Path) -> bool:
    if destination.is_file():
        return False
    # Fail the init container if initial configuration is invalid. Never print it.
    ElectricityAccountPool(source)._read_records()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as output:
            temporary_path = Path(output.name)
            output.write(source.read_bytes())
            output.flush()
            os.fsync(output.fileno())
        # Atomic create-if-absent: an interrupted init cannot leave partial JSON,
        # and simultaneous initialization cannot overwrite an API-owned pool.
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            return False
        return True
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    seed_account_pool(
        Path(os.environ["ELECTRICITY_ACCOUNTS_SEED_FILE"]),
        Path(os.environ["ELECTRICITY_ACCOUNTS_FILE"]),
    )
