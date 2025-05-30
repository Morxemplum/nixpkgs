#!/usr/bin/env nix-shell
#!nix-shell -I nixpkgs=../../../.. -i python3 -p python3

import base64
import json
import logging
import subprocess
import urllib.error
import urllib.request
from contextlib import contextmanager
from operator import itemgetter
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple, Set

# We don't want all those deprecated legacy extensions
# Group extensions by GNOME "major" version for compatibility reasons
supported_versions = {
    "38": "3.38",
    "40": "40",
    "41": "41",
    "42": "42",
    "43": "43",
    "44": "44",
    "45": "45",
    "46": "46",
    "47": "47",
}

# Some type alias to increase readability of complex compound types
PackageName = str
ShellVersion = str
Uuid = str
ExtensionVersion = int

# Keep track of all names that have been used till now to detect collisions.
# This works because we deterministically process all extensions in historical order
# The outer dict level is the shell version, as we are tracking duplicates only per same Shell version.
# key: shell version, value: Dict with key: pname, value: list of UUIDs with that pname
package_name_registry: Dict[ShellVersion, Dict[PackageName, List[Uuid]]] = {}
for shell_version in supported_versions.keys():
    package_name_registry[shell_version] = {}

updater_dir_path = Path(__file__).resolve().parent


def fetch_extension_data(uuid: str, version: str) -> Tuple[str, str]:
    """
    Download the extension and hash it. We use `nix-prefetch-url` for this for efficiency reasons.
    Returns a tuple with the hash (Nix-compatible) of the zip file's content and the base64-encoded content of its metadata.json.
    """

    # The download URLs follow this schema
    uuid = uuid.replace("@", "")
    url: str = f"https://extensions.gnome.org/extension-data/{uuid}.v{version}.shell-extension.zip"

    # Download extension and add the zip content to nix-store
    for _ in range(0, 10):
        process = subprocess.run(
            ["nix-prefetch-url", "--unpack", "--print-path", url], capture_output=True, text=True
        )
        if process.returncode == 0:
            break
        else:
            logging.warning(f"Nix-prefetch-url failed for {url}:")
            logging.warning(f"Stderr: {process.stderr}")
            logging.warning(f"Retrying")

    if process.returncode != 0:
        raise Exception("Retried 10 times, but still failed to download the extension. Exiting.")

    lines = process.stdout.splitlines()

    # Get hash from first line of nix-prefetch-url output
    hash = lines[0].strip()

    # Get path from second line of nix-prefetch-url output
    path = Path(lines[1].strip())

    # Get metadata.json content from nix-store
    with open(path / "metadata.json", "r") as out:
        metadata = base64.b64encode(out.read().encode("ascii")).decode()

    return hash, metadata


def generate_extension_versions(
        extension_version_map: Dict[ShellVersion, ExtensionVersion], uuid: str
) -> Dict[ShellVersion, Dict[str, str]]:
    """
    Takes in a mapping from shell versions to extension versions and transforms it the way we need it:
    - Only take one extension version per GNOME Shell major version (as per `supported_versions`)
    - Filter out versions that only support old GNOME versions
    - Download the extension and hash it
    """

    # Determine extension version per shell version
    extension_versions: Dict[ShellVersion, ExtensionVersion] = {}
    for shell_version, version_prefix in supported_versions.items():
        # Newest compatible extension version
        extension_version: Optional[int] = max(
            (
                int(ext_ver)
                for shell_ver, ext_ver in extension_version_map.items()
                if (shell_ver.startswith(version_prefix))
            ),
            default=None,
        )
        # Extension is not compatible with this GNOME version
        if not extension_version:
            continue

        extension_versions[shell_version] = extension_version

    # Download information once for all extension versions chosen above
    extension_info_cache: Dict[ExtensionVersion, Tuple[str, str]] = {}
    for extension_version in sorted(set(extension_versions.values())):
        logging.debug(
            f"[{uuid}] Downloading v{extension_version}"
        )
        extension_info_cache[extension_version] = \
            fetch_extension_data(uuid, str(extension_version))

    # Fill map
    extension_versions_full: Dict[ShellVersion, Dict[str, str]] = {}
    for shell_version, extension_version in extension_versions.items():
        sha256, metadata = extension_info_cache[extension_version]

        extension_versions_full[shell_version] = {
            "version": str(extension_version),
            "sha256": sha256,
            # The downloads are impure, their metadata.json may change at any time.
            # Thus, we back it up / pin it to remain deterministic
            # Upstream issue: https://gitlab.gnome.org/Infrastructure/extensions-web/-/issues/137
            "metadata": metadata,
        }
    return extension_versions_full


def pname_from_url(url: str) -> Tuple[str, str]:
    """
    Parse something like "/extension/1475/battery-time/" and output ("battery-time", "1475")
    """

    url = url.split("/")  # type: ignore
    return url[3], url[2]


def process_extension(extension: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Process an extension. It takes in raw scraped data and downloads all the necessary information that buildGnomeExtension.nix requires

        Input: a json object of one extension queried from the site. It has the following schema (only important key listed):
            {
                "uuid": str,
                "name": str,
                "description": str,
                "link": str,
                "shell_version_map": {
                    str: { "version": int, … },
                    …
                },
                …
            }

            "uuid" is an extension UUID that looks like this (most of the time): "extension-name@username.domain.tld".
                   Don't make any assumptions on it, and treat it like an opaque string!
            "link" follows the following schema: "/extension/$number/$string/"
                   The number is monotonically increasing and unique to every extension.
                   The string is usually derived from the extension name (but shortened, kebab-cased and URL friendly).
                   It may diverge from the actual name.
            The keys of "shell_version_map" are GNOME Shell version numbers.

        Output: a json object to be stored, or None if the extension should be skipped. Schema:
            {
                "uuid": str,
                "name": str,
                "pname": str,
                "description": str,
                "link": str,
                "shell_version_map": {
                    str: { "version": int, "sha256": str, "metadata": <hex> },
                    …
                }
            }

            Only "uuid" gets passed along unmodified. "name", "description" and "link" are taken from the input, but sanitized.
            "pname" gets generated from other fields and "shell_version_map" has a completely different structure than the input
            field with the same name.
    """
    uuid = extension["uuid"]

    # Yeah, there are some extensions without any releases
    if not extension["shell_version_map"]:
        return None
    logging.info(f"Processing '{uuid}'")

    # Input is a mapping str -> { version: int, … }
    # We want to map shell versions to extension versions
    shell_version_map: Dict[ShellVersion, int] = {
        k: v["version"] for k, v in extension["shell_version_map"].items()
    }
    # Transform shell_version_map to be more useful for us. Also throw away unwanted versions
    shell_version_map: Dict[ShellVersion, Dict[str, str]] = generate_extension_versions(shell_version_map, uuid)  # type: ignore

    # No compatible versions found
    if not shell_version_map:
        return None

    # Fetch a human-readable name for the package.
    (pname, _pname_id) = pname_from_url(extension["link"])

    for shell_version in shell_version_map.keys():
        if pname in package_name_registry[shell_version]:
            logging.warning(f"Package name '{pname}' for GNOME '{shell_version}' is colliding.")
            package_name_registry[shell_version][pname].append(uuid)
        else:
            package_name_registry[shell_version][pname] = [uuid]

    return {
        "uuid": uuid,
        "name": extension["name"],
        "pname": pname,
        "description": extension["description"],
        "link": "https://extensions.gnome.org" + extension["link"],
        "shell_version_map": shell_version_map,
    }


@contextmanager
def request(url: str, retries: int = 5, retry_codes: List[int] = [ 500, 502, 503, 504 ]):
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url) as response:
                yield response
                break
        except urllib.error.HTTPError as e:
            if e.code in retry_codes and attempt < retries:
                logging.warning(f"Error while fetching {url}. Retrying: {e}")
            else:
                raise e


def scrape_extensions_index() -> List[Dict[str, Any]]:
    """
    Scrape the list of extensions by sending search queries to the API. We simply go over it
    page by page until we hit a non-full page or a 404 error.

    The returned list is sorted by the age of the extension, in order to be deterministic.
    """
    page = 0
    extensions = []
    while True:
        page += 1
        logging.info("Scraping page " + str(page))
        try:

            with request(
                    f"https://extensions.gnome.org/extension-query/?n_per_page=25&page={page}"
            ) as response:
                data = json.loads(response.read().decode())["extensions"]
                response_length = len(data)

                for extension in data:
                    extensions.append(extension)

                # If our page isn't "full", it must have been the last one
                if response_length < 25:
                    logging.debug(
                        f"\tThis page only has {response_length} entries, so it must be the last one."
                    )
                    break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # We reached past the last page and are done now
                break
            else:
                raise

    # `pk` is the primary key in the extensions.gnome.org database. Sorting on it will give us a stable,
    # deterministic ordering.
    extensions.sort(key=itemgetter("pk"))
    return extensions


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    raw_extensions = scrape_extensions_index()

    logging.info(f"Downloaded {len(raw_extensions)} extensions. Processing …")
    processed_extensions: List[Dict[str, Any]] = []
    for num, raw_extension in enumerate(raw_extensions):
        processed_extension = process_extension(raw_extension)
        if processed_extension:
            processed_extensions.append(processed_extension)
            logging.debug(f"Processed {num + 1} / {len(raw_extensions)}")

    # We micro-manage a lot of the serialization process to keep the diffs optimal.
    # We generally want most of the attributes of an extension on one line,
    # but then each of its supported versions with metadata on a new line.
    with open(updater_dir_path / "extensions.json", "w") as out:
        for index, extension in enumerate(processed_extensions):
            # Manually pretty-print the outermost array level
            if index == 0:
                out.write("[ ")
            else:
                out.write(", ")
            # Dump each extension into a single-line string forst
            extension = json.dumps(extension, ensure_ascii=False)
            # Inject line breaks for each supported version
            for version in supported_versions:
                # This one only matches the first entry
                extension = extension.replace(f"{{\"{version}\": {{", f"{{\n    \"{version}\": {{")
                # All other entries
                extension = extension.replace(f", \"{version}\": {{", f",\n    \"{version}\": {{")
            # One last line break around the closing braces
            extension = extension.replace("}}}", "}\n  }}")

            out.write(extension)
            out.write("\n")
        out.write("]\n")

    logging.info(
        f"Done. Writing results to extensions.json ({len(processed_extensions)} extensions in total)"
    )

    with open(updater_dir_path / "extensions.json", "r") as out:
        # Check that the generated file actually is valid JSON, just to be sure
        json.load(out)

    with open(updater_dir_path / "collisions.json", "w") as out:
        # Find the name collisions only for the last 3 shell versions
        last_3_versions = sorted(supported_versions.keys(), key=lambda v: float(v), reverse=True)[:3]
        package_name_registry_for_versions = [v for k, v in package_name_registry.items() if k in last_3_versions]
        # Merge all package names into a single dictionary
        package_name_registry_filtered: Dict[PackageName, Set[Uuid]] = {}
        for pkgs in package_name_registry_for_versions:
            for pname, uuids in pkgs.items():
                if pname not in package_name_registry_filtered:
                    package_name_registry_filtered[pname] = set()
                package_name_registry_filtered[pname].update(uuids)
        # Filter out those that are not duplicates
        package_name_registry_filtered = {k: v for k, v in package_name_registry_filtered.items() if len(v) > 1}
        # Convert set to list
        collisions: Dict[PackageName, List[Uuid]] = {k: list(v) for k, v in package_name_registry_filtered.items()}
        json.dump(collisions, out, indent=2, ensure_ascii=False)
        out.write("\n")

    logging.info(
        "Done. Writing name collisions to collisions.json (please check manually)"
    )
