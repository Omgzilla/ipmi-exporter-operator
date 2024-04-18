#!/usr/bin/env python3
# Copyright 2024 marcus
# See LICENSE file for licensing details.
# Learn more at: https://juju.is/docs/sdk

"""Prometheus IPMI Exporter Charm"""

import logging
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import request

from jinja2 import Environment, FileSystemLoader
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus

from interface_prometheus import Prometheus

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

class IpmiExporterOperatorCharm(CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        """Initialize charm"""
        super().__init__(*args)

        self.prometheus = Prometheus(self, "prometheus")

        # juju core hooks
        self.framwork.observe(self.on.install, self._on_install)
        self.framwork.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framwork.observe(self.on.config_changed, self._on_config_changed)
        self.framwork.observe(self.on.start, self._on_start)
        self.framwork.observe(self.on.stop, self._on_stop)



    @property
    def port(self):
        """Return the port that ipmi-exporter listens to"""
        return self.model.config.get("listen-address").split(":")[1]

    def _on_install(self, event):
        logger.debug("## Installing charm")
        self.unit.status = MaintenanceStatus("Installing ipmi-exporter")
        self._set_charm_verion()
        _install_ipmi_exporter(self.model.config.get("ipmi-exporter-version"))

        self.unit.status = ActiveStatus("ipmi-exporter installed")

    def _on_upgrade_charm(self, event):
        """Perform upgrade operation"""
        logger.debug("## Upgrading charm")
        self.unit.status = MaintenanceStatus("Upgrading ipmi-exporter")
        self._set_charm_verion()

        self.unit.status = ActiveStatus("ipmi-exporter upgraded")

    def _on_config_changed(self, event):
        """Handle configuration updates"""
        logger.debug("## Configuring charm")

        params = dict()
        params["listen_address"] = self.model.config.get("listen-address")
        
        logger.debug(f"## Configuration options: {params}")
        _render_sysconfig(params)
        subprocess.call(["systemctl", "restart", "ipmi_exporter"])

        self.prometheus.set_host_port()

    def _on_start(self, event):
        logger.debug("## Starting daemon")
        subprocess.call(["systemctl", "start", "ipmi_exporter"])
        self.unit.status = ActiveStatus("ipmi-exporter started")

    def _on_stop(self, event):
        logger.debug("## Stopping daemon")
        subprocess.call("systemctl", "stop", "ipmi_exporter")
        subprocess.call("systemctl", "disable", "ipmi_exporter")
        _uninstall_ipmi_exporter()

    def _on_update_status(self, _):
        self.update_status()

    def update_status(self):
        """ Checks if the service is running. """
        attempts = 5
        for i in range(5):
            service_started = os.system(f"service {SERVICE_NAME} status")
            if service_started != 0:
                self.unit.status = ops.WaitingStatus(f'Service not yet started. Attempt {i}/{attempts}')
                time.sleep(4)
            else:
                self.unit.status = ops.ActiveStatus("Service running.")
                return
        self.unit.status = ops.BlockedStatus('Service not running!')

    def _set_charm_verion(self):
        """Set the application version"""
        self.unit.set_workload_version(Path("version").read_text().strip())

def _install_ipmi_exporter(version: str, arch: str = "amd64"):
    """Download appropriate files and install ipmi-exporter

    This function downloads ipmi-exporter tarfile, extracts it to /usr/bin/,
    create ipmi-exporter user and group, creates the systemd service unit.

    Args:
        version: a string representing the version to install.
        arch: the hardware architecture (e.g. amd64, armv7).
    """

    logger.debug(f"## Installing ipmi_exporter version {version}")

    # Download file
    url = f"https://github.com/prometheus-community/ipmi_exporter/releases/download/v{version}/ipmi_exporter-{version}.linux-{arch}.tar.gz"
    logger.debug(f"## Downloading {url}")
    output = Path("/tmp/ipmi-exporter.tar.gz")
    fname, headers = request.urlretrieve(url, output)

    # Extract file
    tar = tarfile.open(output, 'r')
    with TemporaryDirectory(prefix="dwellir") as tmp_dir:
        logger.debug(f"## Extracting {tar} to {tmp_dir}")
        tar.extractall(path=tmp_dir)

        logger.debug("## Installing ipmi_exporter")
        source = Path(tmp_dir) / f"ipmi_exporter-{version}.linux-{arch}/ipmi_exporter"
        shutil.copy2(source, "/usr/bin/ipmi_exporter")

    # Clean up
    output.unlink()

    _create_ipmi_exporter_user_group()
    _create_systemd_service_unit()
    _render_sysconfig({"listen_address": "0.0.0.0:9290"})

def _install_freeipmi():
    """Install freeIPMI"""
    logger.debug("## Installing freeIPMI")
    # Update and install freeipmi through apt
    subprocess.run("apt update -y".split())
    subprocess.run("apt install freeipmi -y".split())
    self.update_status()
    

def _create_ipmi_exporter_user_group():
    logger.debug("## Create ipmi_exporter group")
    group = "ipmi_exporter"
    cmd = f"groupadd {group}"
    subprocess.call(shlex.split(cmd))

    logger.debug("## Create ipmi_exporter user")
    user = "ipmi_exporter"
    cmd = f"useradd --system --no-create-home --gid {group} --shell /usr/sbin/nologin {user}"
    subprocess.call(shlex.split(cmd))

def _create_systemd_service_unit():
    logger.debug("## Create systemd service unit for ipmi_exporter")
    charm_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = Path(charm_dir) / "assets"

    service = "ipmi_exporter.service"
    shutil.copyfile(template_dir / service, f"/etc/systemd/system/{service}")

    subprocess.call(["systemctl", "daemon-reload"])
    subprocess.call(["systemctl", "enable", service])

def _render_sysconfig(context: dict) -> None:
    """Render the sysconfig file,
    'context' should contain the following keys:
        listen_address: a string specifying the address to listen to, e.g. 0.0.0.0:9290

    """
    logger.debug("## Writing sysconfig file")

    charm_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = Path(charm_dir) / "assets"
    temp_conf = "ipmi_exporter.yaml"
    temp_opt = "ipmi_exporter"

    environment = Environment(loader=FileSystemLoader(template_dir))
    template_conf = environment.get_template(temp_conf)
    template_opt = environment.get_template(temp_opt)

    sysconfig = Path("/etc/default/")
    if not sysconfig.exists():
        sysconfig.mkdir()

    target_opt = sysconfig / temp_opt
    if target_opt.exists():
        target_opt.unlink()
    target_opt.write_text(template_opt.render(context))

    varlib = Path("/var/lib/ipmi_exporter")
    if not varlib.exists():
        varlib.mkdir(parents=True)
    shutil.chown(varlib, user="ipmi_exporter", group="ipmi_exporter")

    target_conf = varlib / "ipmi_exporter"
    if target_conf.exists():
        target_conf.unlink()
    target_conf.write_text(template_conf.render(context))
    

def _uninstall_ipmi_exporter():
    logger.debug("## Uninstalling ipmi-exporter")

    # Remove files and folders
    Path("/usr/bin/ipmi_exporter").unlink()
    Path("/etc/systemd/system/ipmi_exporter.service").unlink()
    Path("/etc/default/ipmi_exporter").unlink()
    shutil.rmtree(Path("/var/lib/ipmi_exporter"))

    # Remove user and group
    user = "ipmi_exporter"
    group = "ipmi_exporter"
    subprocess.call(["userdel", user])
    subprocess.call(["groupdel", group])

if __name__ == "__main__":
    main(IpmiExporterOperatorCharm)
