from src.connectors.base import Connector
from src.connectors.woocommerce.connector import WooCommerceConnector

CONNECTOR_REGISTRY: dict[str, type[Connector]] = {
    "woocommerce": WooCommerceConnector,
}
