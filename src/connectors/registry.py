"""Registro global de conectores disponibles.

Para agregar un nuevo conector:
1. Crear ``src/connectors/<nombre>/connector.py`` con la clase concreta.
2. Agregar la función de carga diferida y registrarla en ``_build_registry``.
3. Nada más — el núcleo del Hub lo descubrirá automáticamente.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.connectors.base import Connector

# Importaciones diferidas para evitar ciclos y permitir que cada conector
# sea opcional (podría no tener su dep instalada).
def _load_woocommerce():
    from src.connectors.woocommerce.connector import WooCommerceConnector
    return WooCommerceConnector


def _load_shopify():
    from src.connectors.shopify.connector import ShopifyConnector
    return ShopifyConnector


CONNECTOR_REGISTRY: dict[str, type["Connector"]] = {}


def _build_registry() -> dict[str, type["Connector"]]:
    reg: dict[str, type["Connector"]] = {}
    try:
        reg["woocommerce"] = _load_woocommerce()
    except Exception:
        pass
    try:
        reg["shopify"] = _load_shopify()
    except Exception:
        pass
    return reg


def get_registry() -> dict[str, type["Connector"]]:
    """Retorna el registro completo de conectores disponibles."""
    if not CONNECTOR_REGISTRY:
        CONNECTOR_REGISTRY.update(_build_registry())
    return CONNECTOR_REGISTRY


def get_connector_class(name: str) -> type["Connector"]:
    """Retorna la clase del conector ``name`` o lanza ``KeyError``."""
    reg = get_registry()
    if name not in reg:
        raise KeyError(f"Conector '{name}' no registrado. Disponibles: {list(reg)}")
    return reg[name]
