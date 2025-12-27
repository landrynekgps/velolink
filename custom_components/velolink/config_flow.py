"""Config flow."""

import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from .const import CONF_GATEWAY_HOST, CONF_GATEWAY_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)


class VelolinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Menu startowy."""
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "scan_network": "Skanuj sieć (mDNS)",
                "manual_setup": "Ręczne wpisanie IP/Nazwy",
            },
        )

    async def async_step_scan_network(self, user_input=None) -> FlowResult:
        """Prośba o wpisanie nazwy hosta mDNS."""
        if user_input is not None:
            host = user_input[CONF_GATEWAY_HOST]
            # Formatujemy unique_id jako host (lub host + port)
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()

            data = {CONF_GATEWAY_HOST: host, CONF_GATEWAY_PORT: 5485}
            return self.async_create_entry(title=f"Velolink Device ({host})", data=data)

        schema = vol.Schema(
            {vol.Required(CONF_GATEWAY_HOST, default="velolink-gateway.local"): str}
        )
        return self.async_show_form(
            step_id="scan_network",
            data_schema=schema,
            description_placeholders={
                "info": "Wpisz nazwę hosta mDNS (np. velolink-gateway.local lub velolink-dev-a4b1.local)"
            },
        )

    async def async_step_manual_setup(self, user_input=None) -> FlowResult:
        if user_input is not None:
            host = user_input[CONF_GATEWAY_HOST]
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=f"Velolink ({host})", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_GATEWAY_HOST): str,
                vol.Required(CONF_GATEWAY_PORT, default=5485): int,
            }
        )
        return self.async_show_form(step_id="manual_setup", data_schema=schema)
