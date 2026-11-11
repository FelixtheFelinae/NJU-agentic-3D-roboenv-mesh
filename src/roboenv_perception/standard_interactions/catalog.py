"""Read immutable candidate data; do not adapt standard parameters per asset."""

import hashlib
import json
from importlib.resources import files


class StandardComponentError(ValueError):
    """A standard or runtime defect must not trigger object geometry regeneration."""

    failure_domain = "standard_primitive"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def load_catalog():
    data = json.loads(files(__package__).joinpath("catalog_v1.json").read_text())
    if digest(data) != CATALOG_SHA256:
        raise StandardComponentError("standard catalog changed; publish a new explicit version")
    return data


def resolve(section, reference):
    try:
        return load_catalog()[section][reference]
    except KeyError as error:
        raise StandardComponentError(f"unregistered {section} reference: {reference}") from error


# Changes to the catalog require an explicit version and lock update.
CATALOG_SHA256 = "7f4cb5bb71c624e76ca7ce788fbe9afffeb9ec163cbb0779aee201f0e996c6ad"


def verify_runtime_sources(runtime):
    """The installed mechanics code must match the registered snapshot."""
    for record in runtime["source_files"]:
        source = files("roboenv_perception.tools").joinpath(record["path"].rsplit("/", 1)[-1])
        if hashlib.sha256(source.read_bytes()).hexdigest() != record["sha256"]:
            raise StandardComponentError(
                "runtime source changed without a new version: " + record["path"]
            )
