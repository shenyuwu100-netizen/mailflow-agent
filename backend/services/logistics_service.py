"""
Logistics pricing service.
Queries route-specific pricing from the logistics_routes database.
"""

from models.database import get_db_connection
from utils.logger import get_logger

logger = get_logger('logistics_service')


class RouteNotFoundError(Exception):
    """Raised when a logistics route is not found in the database."""
    pass


class LogisticsService:
    """Service for querying logistics route pricing."""

    def query_route_pricing(
        self,
        origin: str,
        destination: str,
        shipping_method: str,
        container_type: str = None,
        weight_kg: float = None
    ) -> dict:
        """
        Query logistics pricing for a specific route.

        Args:
            origin: Origin city (e.g., "深圳", "上海")
            destination: Destination city (e.g., "纽约", "伦敦")
            shipping_method: "sea_freight" or "air_freight"
            container_type: For sea freight (e.g., "20ft", "40ft")
            weight_kg: For air freight (e.g., 100)

        Returns:
            dict with route pricing information

        Raises:
            RouteNotFoundError: If no matching route found
        """
        with get_db_connection() as conn:
            conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

            # Normalize city names for matching
            origin_normalized = self._normalize_city_name(origin)
            destination_normalized = self._normalize_city_name(destination)

            # Query for matching route
            query = """
                SELECT * FROM logistics_routes
                WHERE LOWER(origin) = LOWER(?)
                  AND LOWER(destination) = LOWER(?)
                  AND shipping_method = ?
            """
            params = [origin_normalized, destination_normalized, shipping_method]

            # Add container/weight filters if provided
            if shipping_method == 'sea_freight' and container_type:
                query += " AND (container_type = ? OR container_type IS NULL)"
                params.append(container_type)
            elif shipping_method == 'air_freight' and weight_kg:
                query += " AND (weight_range IS NULL OR ? BETWEEN CAST(SUBSTR(weight_range, 1, INSTR(weight_range, '-')-1) AS INTEGER) AND CAST(SUBSTR(weight_range, INSTR(weight_range, '-')+1) AS INTEGER))"
                params.append(weight_kg)

            query += " ORDER BY price ASC LIMIT 1"

            cursor = conn.execute(query, params)
            route = cursor.fetchone()

            if not route:
                logger.info("Route not found in database", {
                    'origin': origin,
                    'destination': destination,
                    'shipping_method': shipping_method
                })
                raise RouteNotFoundError(
                    f"No pricing found for route: {origin} -> {destination} ({shipping_method})"
                )

            logger.info("Route pricing found", {
                'origin': route['origin'],
                'destination': route['destination'],
                'price': route['price'],
                'currency': route['currency']
            })

            return route

    def format_route_pricing(self, route: dict, language: str = "zh") -> str:
        """Format route pricing information for email reply."""
        if language == "zh":
            shipping_method_label = {
                'sea_freight': '海运',
                'air_freight': '空运'
            }.get(route['shipping_method'], route['shipping_method'])

            lines = [
                f"- 运输方式：{shipping_method_label}",
                f"- 起运地：{route['origin']}",
                f"- 目的地：{route['destination']}",
            ]

            if route.get('container_type'):
                lines.append(f"- 柜型：{route['container_type']}")

            if route.get('weight_range'):
                lines.append(f"- 重量范围：{route['weight_range']} kg")

            lines.extend([
                f"- 运费：{route['currency']} {route['price']}",
            ])

            if route.get('transit_days'):
                lines.append(f"- 运输时效：约 {route['transit_days']} 天")

            return "\n".join(lines)

        return str(route)

    @staticmethod
    def _normalize_city_name(city: str) -> str:
        """Normalize city names for consistent matching."""
        # Map common variations to standard names
        city_map = {
            '深圳': '深圳',
            'shenzhen': '深圳',
            '上海': '上海',
            'shanghai': '上海',
            '纽约': '纽约',
            'new york': '纽约',
            'newyork': '纽约',
            '伦敦': '伦敦',
            'london': '伦敦',
            '洛杉矶': '洛杉矶',
            'los angeles': '洛杉矶',
            'la': '洛杉矶',
            '香港': '香港',
            'hong kong': '香港',
            'hongkong': '香港',
        }

        city_lower = city.lower().strip()
        return city_map.get(city_lower, city)


# Singleton instance
_logistics_service = None


def get_logistics_service() -> LogisticsService:
    """Get or create the singleton LogisticsService instance."""
    global _logistics_service
    if _logistics_service is None:
        _logistics_service = LogisticsService()
    return _logistics_service
