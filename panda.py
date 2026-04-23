#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
panda.py — Pardus Alternative Driver Administration
====================================================
Manages proprietary vs. open-source GPU driver selection on Pardus Linux.
Handles GRUB kernel parameter editing and pisi package queries.

Usage (as a library):
    from panda import Panda
    p = Panda()
    print(p.get_driver_state())

Usage (CLI):
    python3 panda.py --state
    python3 panda.py --set vendor
    python3 panda.py --packages
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("panda")

# ---------------------------------------------------------------------------
# File-system paths (kept as constants so tests can monkey-patch them)
# ---------------------------------------------------------------------------
SYSDIR       = Path("/sys/bus/pci/devices/")
DRIVERS_DB   = Path("/usr/share/X11/DriversDB")
GRUB_FILE    = Path("/boot/grub/grub.conf")
GRUB_NEW     = Path("/boot/grub/grub.conf.new")
GRUB_BACKUP  = Path("/boot/grub/grub.conf.back")
KERNEL_DIR   = Path("/etc/kernel/")

# ---------------------------------------------------------------------------
# Driver → package mapping
# ---------------------------------------------------------------------------
DRIVER_PACKAGES: dict[str, list[str]] = {
    "fglrx": [
        "module-fglrx",
        "module-pae-fglrx",
        "module-fglrx-userspace",
        "xorg-video-fglrx",
    ],
    "nvidia-current": [
        "module-nvidia-current",
        "module-pae-nvidia-current",
        "module-nvidia-current-userspace",
        "xorg-video-nvidia-current",
        "nvidia-xconfig",
        "nvidia-settings",
    ],
    "nvidia96": [
        "module-nvidia96",
        "module-pae-nvidia96",
        "module-nvidia96-userspace",
        "xorg-video-nvidia96",
        "nvidia-xconfig",
        "nvidia-settings",
    ],
    "nvidia173": [
        "module-nvidia173",
        "module-pae-nvidia173",
        "module-nvidia173-userspace",
        "xorg-video-nvidia173",
        "nvidia-xconfig",
        "nvidia-settings",
    ],
}

# Open-source kernel modules that must be blacklisted for each proprietary driver
OS_DRIVER_MAP: dict[str, str] = {
    "fglrx":           "radeon",
    "nvidia-current":  "nouveau",
    "nvidia96":        "nouveau",
    "nvidia173":       "nouveau",
}

VALID_STATES = ("vendor", "os", "generic")


# ---------------------------------------------------------------------------
# Helper: safe file read
# ---------------------------------------------------------------------------
def _read_file(path: Path, default: str = "") -> str:
    """Read a text file and return its content, or *default* on any error."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.debug("Cannot read %s: %s", path, exc)
        return default


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------
class Panda:
    """Pardus Alternative Driver Administration.

    Detects the primary GPU driver, maps it to the correct pisi packages,
    and can read/write GRUB kernel parameters to switch between:

      * ``vendor``  — proprietary driver (fglrx / nvidia-*); OS driver blacklisted
      * ``os``      — open-source driver (radeon / nouveau)
      * ``generic`` — VESA/safe mode (xorg safe + nomodeset)
    """

    def __init__(self) -> None:
        self._driver_name:   Optional[str]        = None   # e.g. "nvidia-current"
        self._kernel_flavors: Optional[dict[str, str]] = None  # e.g. {"kernel": "4.4.0-1"}
        self._os_driver:     Optional[str]        = None   # e.g. "nouveau"

    # ------------------------------------------------------------------
    # Private: hardware / kernel detection
    # ------------------------------------------------------------------

    def _get_primary_driver(self) -> str:
        """Detect the driver name for the boot VGA device.

        Reads ``/sys/bus/pci/devices/*/boot_vga`` to find the primary GPU,
        then looks up its PCI ID in the DriversDB file.

        Returns the driver name string, or ``"unknown"`` if nothing matched.
        """
        driver = "unknown"

        for boot_vga_path in SYSDIR.glob("*/boot_vga"):
            if _read_file(boot_vga_path) != "1":
                continue

            dev_path = boot_vga_path.parent
            vendor   = _read_file(dev_path / "vendor")
            device   = _read_file(dev_path / "device")

            if not vendor or not device:
                log.warning("Could not read vendor/device ID from %s", dev_path)
                break

            # PCI ID without the '0x' prefix, e.g. "10de1c82"
            device_id = vendor[2:] + device[2:]
            vendor_id = vendor[2:]

            # Fallback for Nvidia cards not yet in DriversDB
            if vendor_id == "10de":
                driver = "nvidia-current"
                log.debug("Nvidia vendor detected; defaulting to nvidia-current")

            if not DRIVERS_DB.exists():
                log.warning("DriversDB not found at %s", DRIVERS_DB)
                break

            with DRIVERS_DB.open(encoding="utf-8", errors="replace") as db:
                for line in db:
                    line = line.strip()
                    if line.startswith(device_id):
                        parts = line.split()
                        if len(parts) >= 2:
                            driver = parts[1]
                            log.info("Driver matched from DriversDB: %s", driver)
                        break

            break  # We only care about the primary (boot) GPU

        self._driver_name = driver
        return driver

    def _get_kernel_flavors(self) -> dict[str, str]:
        """Return a dict of kernel-name → kernel-version from ``/etc/kernel/``."""
        flavors: dict[str, str] = {}
        for kfile in KERNEL_DIR.glob("*"):
            if kfile.is_file():
                flavors[kfile.name] = _read_file(kfile)

        if not flavors:
            log.warning("No kernel files found in %s", KERNEL_DIR)

        self._kernel_flavors = flavors
        return flavors

    def _kernel_module_packages(self, kernel_list: Optional[list[str]] = None) -> list[str]:
        """Return the kernel-module package names for the detected driver."""
        if kernel_list is None:
            kernel_list = list(self._ensure_kernel_flavors().keys())

        driver = self._ensure_driver()
        if driver == "unknown":
            return []

        packages: list[str] = []
        for kernel_name in kernel_list:
            _base, sep, suffix = kernel_name.partition("-")
            if sep and suffix:
                packages.append(f"module-{suffix}-{driver}")
            else:
                packages.append(f"module-{driver}")

        return packages

    # ------------------------------------------------------------------
    # Private: lazy initialisation helpers
    # ------------------------------------------------------------------

    def _ensure_driver(self) -> str:
        if self._driver_name is None:
            self._get_primary_driver()
        return self._driver_name  # type: ignore[return-value]

    def _ensure_os_driver(self) -> Optional[str]:
        if self._os_driver is None:
            self.get_blacklisted_module()
        return self._os_driver

    def _ensure_kernel_flavors(self) -> dict[str, str]:
        if self._kernel_flavors is None:
            self._get_kernel_flavors()
        return self._kernel_flavors  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def driver_name(self) -> str:
        """Name of the detected primary GPU driver (e.g. ``"nvidia-current"``)."""
        return self._ensure_driver()

    def get_blacklisted_module(self) -> Optional[str]:
        """Return the open-source kernel module that must be blacklisted.

        For example, ``"nouveau"`` for any Nvidia driver, ``"radeon"`` for fglrx.
        Returns ``None`` if no blacklisting is needed (generic/unknown driver).
        """
        driver = self._ensure_driver()
        self._os_driver = OS_DRIVER_MAP.get(driver)

        if self._os_driver:
            log.debug("OS driver to blacklist: %s", self._os_driver)
        else:
            log.debug("No OS driver to blacklist for driver: %s", driver)

        return self._os_driver

    def get_driver_types(self) -> list[str]:
        """Return the list of available driver *states* for the current GPU.

        * Proprietary-capable GPU → ``["vendor", "os", "generic"]``
        * Unknown / unsupported   → ``["os", "generic"]``
        """
        driver = self._ensure_driver()
        if driver in DRIVER_PACKAGES:
            return ["vendor", "os", "generic"]
        return ["os", "generic"]

    def get_needed_driver_packages(
        self,
        kernel_flavors: Optional[list[str]] = None,
        installable: bool = False,
    ) -> list[str]:
        """Return the list of pisi packages needed for the current driver.

        Parameters
        ----------
        kernel_flavors:
            Override the detected kernel list. ``None`` uses auto-detection.
        installable:
            If ``True``, exclude packages that are already installed.
        """
        driver = self._ensure_driver()
        if driver == "unknown" or driver not in DRIVER_PACKAGES:
            log.info("No proprietary driver detected; no extra packages needed.")
            return []

        needed_module_pkgs = set(self._kernel_module_packages(kernel_flavors))
        all_module_pkgs = {
            p for p in DRIVER_PACKAGES[driver]
            if p.startswith("module-") and not p.endswith("-userspace")
        }

        # Keep everything in the driver set *except* module packages that
        # are NOT needed for the currently installed kernels.
        excluded = all_module_pkgs - needed_module_pkgs
        to_install = [p for p in DRIVER_PACKAGES[driver] if p not in excluded]

        if installable:
            try:
                import pisi.db.installdb  # type: ignore[import]
                idb = pisi.db.installdb.InstallDB()
                to_install = [p for p in to_install if not idb.has_package(p)]
            except ImportError:
                log.error("pisi is not available; cannot filter installed packages.")

        return to_install

    def get_all_driver_packages(self) -> list[str]:
        """Return a deduplicated list of every package across all known drivers."""
        return list({pkg for pkgs in DRIVER_PACKAGES.values() for pkg in pkgs})

    # ------------------------------------------------------------------
    # GRUB line parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _param_values_in_line(line: str, keyword: str) -> list[str]:
        """Extract comma-separated values for a kernel parameter.

        For example, given ``keyword="blacklist"`` and a line containing
        ``blacklist=radeon,nouveau``, returns ``["radeon", "nouveau"]``.
        """
        values: list[str] = []
        for param in line.split():
            if param.startswith(f"{keyword}="):
                values.extend(param.split("=", 1)[1].split(","))
        return values

    @staticmethod
    def _update_param_in_line(
        line: str,
        name: str,
        value: bool | list[str] | None,
    ) -> str:
        """Rewrite a GRUB kernel line with a modified parameter.

        Parameters
        ----------
        line:   The original kernel line.
        name:   Parameter name (e.g. ``"blacklist"``).
        value:
            * ``True``          → append bare flag (``nomodeset``)
            * ``False`` / ``None`` / empty list → remove the parameter entirely
            * non-empty list    → ``name=val1,val2,…``
        """
        # Strip the old occurrence of this parameter
        params = [p for p in line.strip().split() if not p.startswith(name)]

        if value is True:
            params.append(name)
        elif value:  # non-empty list
            params.append(f"{name}={','.join(value)}")
        # else: value is False / None / [] → parameter removed

        return " ".join(params) + "\n"

    # ------------------------------------------------------------------
    # GRUB state reading / writing
    # ------------------------------------------------------------------

    def get_driver_state(self) -> str:
        """Read the current driver state from the GRUB configuration file.

        Returns one of ``"vendor"``, ``"os"``, ``"generic"``,
        or raises ``RuntimeError`` on parse failure.
        """
        os_driver = self._ensure_os_driver()
        flavors   = self._ensure_kernel_flavors()

        kernel_version = flavors.get("kernel")
        if not kernel_version:
            raise RuntimeError("Cannot determine kernel version from /etc/kernel/")

        if not GRUB_FILE.exists():
            raise FileNotFoundError(f"GRUB config not found: {GRUB_FILE}")

        with GRUB_FILE.open(encoding="utf-8", errors="replace") as grub:
            for line in grub:
                if "kernel" not in line or kernel_version not in line:
                    continue

                blacklist  = self._param_values_in_line(line, "blacklist")
                xorg_param = self._param_values_in_line(line, "xorg")

                if os_driver and os_driver in blacklist:
                    return "vendor"
                if "safe" in xorg_param:
                    return "generic"
                return "os"

        raise RuntimeError(f"Could not locate a kernel line for version {kernel_version!r} in {GRUB_FILE}")

    def set_driver_state(self, state: str) -> str:
        """Edit the GRUB file to switch to the requested driver *state*.

        Parameters
        ----------
        state:
            One of ``"vendor"``, ``"os"``, or ``"generic"``.

        Returns
        -------
        str
            The new state string on success.

        Raises
        ------
        ValueError
            If *state* is not a valid option.
        RuntimeError
            If the OS driver is required but could not be determined,
            or if the GRUB file could not be parsed.
        """
        if state not in VALID_STATES:
            raise ValueError(f"Invalid state {state!r}. Choose from: {VALID_STATES}")

        os_driver = self._ensure_os_driver()
        if state == "vendor" and os_driver is None:
            raise RuntimeError(
                "Cannot switch to 'vendor' state: no OS driver to blacklist "
                "(is the GPU driver supported?)"
            )

        flavors = self._ensure_kernel_flavors()
        kernel_version = flavors.get("kernel")
        if not kernel_version:
            raise RuntimeError("Cannot determine kernel version from /etc/kernel/")

        if not GRUB_FILE.exists():
            raise FileNotFoundError(f"GRUB config not found: {GRUB_FILE}")

        changed = False

        with GRUB_FILE.open(encoding="utf-8", errors="replace") as grub_in, \
             GRUB_NEW.open("w", encoding="utf-8") as grub_out:

            for line in grub_in:
                if "kernel" not in line or kernel_version not in line:
                    grub_out.write(line)
                    continue

                blacklist  = self._param_values_in_line(line, "blacklist")
                xorg_param = self._param_values_in_line(line, "xorg")

                if state == "os":
                    blacklist   = [x for x in blacklist if x != os_driver]
                    xorg_param  = [x for x in xorg_param if x != "safe"]
                    nomodeset   = False

                elif state == "vendor":
                    if os_driver not in blacklist:
                        blacklist.append(os_driver)  # type: ignore[arg-type]
                    xorg_param  = [x for x in xorg_param if x != "safe"]
                    nomodeset   = False

                elif state == "generic":
                    if "safe" not in xorg_param:
                        xorg_param.append("safe")
                    nomodeset   = True

                new_line = self._update_param_in_line(line,     "xorg",      xorg_param)
                new_line = self._update_param_in_line(new_line, "nomodeset", nomodeset)
                new_line = self._update_param_in_line(new_line, "blacklist", blacklist)

                if new_line != line:
                    changed = True

                grub_out.write(new_line)

        if changed:
            log.info("GRUB config changed. Creating backup at %s", GRUB_BACKUP)
            shutil.copy2(GRUB_FILE, GRUB_BACKUP)
            shutil.copy2(GRUB_NEW, GRUB_FILE)
            log.info("GRUB config updated: %s", GRUB_FILE)
        else:
            log.info("No changes needed; GRUB config already matches state %r.", state)

        # Clean up temp file
        try:
            GRUB_NEW.unlink(missing_ok=True)
        except OSError:
            pass

        return state


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Pardus Alternative Driver Administration (panda)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--state",
        action="store_true",
        help="Print the current driver state (vendor / os / generic).",
    )
    g.add_argument(
        "--set",
        metavar="STATE",
        choices=VALID_STATES,
        help="Switch to the given driver state (vendor, os, generic).",
    )
    g.add_argument(
        "--driver",
        action="store_true",
        help="Print the detected primary GPU driver name.",
    )
    g.add_argument(
        "--packages",
        action="store_true",
        help="List pisi packages needed for the current driver.",
    )
    g.add_argument(
        "--all-packages",
        action="store_true",
        help="List every known pisi package across all drivers.",
    )
    g.add_argument(
        "--blacklisted-module",
        action="store_true",
        help="Print the open-source kernel module that will be blacklisted.",
    )
    p.add_argument(
        "--installable",
        action="store_true",
        default=False,
        help="With --packages: only list packages not yet installed.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    return p


def main() -> int:
    parser = _build_arg_parser()
    args   = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    panda = Panda()

    try:
        if args.state:
            print(panda.get_driver_state())

        elif args.set:
            new_state = panda.set_driver_state(args.set)
            print(f"Driver state set to: {new_state}")

        elif args.driver:
            print(panda.driver_name)

        elif args.packages:
            pkgs = panda.get_needed_driver_packages(installable=args.installable)
            for pkg in pkgs:
                print(pkg)

        elif args.all_packages:
            for pkg in sorted(panda.get_all_driver_packages()):
                print(pkg)

        elif args.blacklisted_module:
            module = panda.get_blacklisted_module()
            print(module if module else "(none)")

    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
