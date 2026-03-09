"""Upgrade Odoo modules using AI."""

import os
from pathlib import Path

from odev.common import args, progress
from odev.common.commands import DatabaseCommand
from odev.common.commands.base import Namespace
from odev.common.databases import LocalDatabase
from odev.common.logging import logging
from odev.common.mixins.databases.list import ListLocalDatabasesMixin
from odev.common.odoobin import OdoobinProcess
from odev.common.version import OdooVersion

from odev.plugins.odev_plugin_ai.common.llm import LLM
from odev.plugins.odev_plugin_ai_upgrade.common.agent import UpgradeAgent

logger = logging.getLogger(__name__)


class UpgradeCommand(DatabaseCommand, ListLocalDatabasesMixin):
    """Upgrades an Odoo module from a previous version to a new version using an AI model.

    This command runs in a loop, attempting to fix errors by editing files and re-running tests.
    """

    _name = "upgrade"
    _database_arg_required = False

    module_name = args.Argument(
        "module_name",
        type=str,
        help="The name of the module to upgrade.",
    )

    from_version = args.Argument(
        "-f",
        "--from-version",
        type=str,
        help="The original Odoo version of the module (e.g., 16.0).",
        default=None,
    )

    target_version = args.Argument(
        "-t",
        "--target-version",
        type=str,
        help="The target Odoo version. Defaults to the environment's target version.",
        default=None,
    )

    max_iterations = args.Argument(
        "--max-iterations",
        type=int,
        help="Maximum number of fix iterations for the agent.",
        default=10,
    )

    @property
    def _database_exists_required(self) -> bool:
        return False

    def __init__(self, args: Namespace, **kwargs):
        super().__init__(args, **kwargs)
        self._load_api_keys()

        # Using the same LLM logic as the scaffold plugin
        self.llm = LLM(getattr(self.args, "llm", None) or "gemini/gemini-pro", self.config.ai.llm_order)

    def run(self) -> None:
        """Execute the upgrade command."""
        module_name = self.args.module_name
        from_ver = self.args.from_version or "previous"
        target_ver = self.args.target_version or str(self.target_version)

        logger.info(f"Starting AI Upgrade for module '{module_name}'")
        logger.info(f"Upgrading from version {from_ver} to {target_ver}")

        # The module is expected to be in the current path or addons path.
        module_path = self.args.path / module_name
        if not module_path.exists():
            logger.error(f"Module '{module_name}' not found at {module_path}")
            return

        agent = UpgradeAgent(
            llm=self.llm,
            module_name=module_name,
            module_path=module_path,
            from_version=from_ver,
            target_version=OdooVersion(target_ver),
            odev_app=self.odev,
            console=self.console,
        )

        spinner = progress.spinner(f"Upgrading {module_name}")
        spinner.start()
        try:
            spinner.update("Starting Agentic Loop...")
            success = agent.run_upgrade_loop(max_iterations=self.args.max_iterations)
            
            if success:
                logger.info("Upgrade completed successfully! All tests passed.")
            else:
                logger.warning(f"Upgrade loop finished or max iterations ({self.args.max_iterations}) reached without full success.")
        finally:
            spinner.stop()

    def _load_api_keys(self) -> None:
        """Load API keys from secrets and update environment variables."""
        api_key_list = {}
        for provider in self.config.ai.llm_order:
            key = f"{provider}_api_key"
            secret = self.odev.store.secrets.get(key.lower(), scope="api", fields=["password"])
            if secret:
                api_key_list[key.upper()] = secret.password
        os.environ.update(api_key_list)
