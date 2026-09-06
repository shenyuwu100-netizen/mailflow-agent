"""
Order management and validation service.
Handles order lookup, validation, and status updates.
"""
from typing import Optional
from models.database import get_db_connection


class OrderNotFoundError(Exception):
    """Raised when order is not found in database."""
    pass


class OrderService:
    """Service for order validation and management."""

    @staticmethod
    def find_order_by_number(order_number: str) -> Optional[dict]:
        """
        Find order by order number.

        Args:
            order_number: Order number to search for

        Returns:
            Order dict if found, None otherwise
        """
        if not order_number:
            return None

        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT id, order_number, customer_email, product_name,
                       quantity, total_amount, currency, order_status,
                       shipping_status, tracking_number, destination,
                       created_at, updated_at
                FROM orders
                WHERE order_number = ?
                """,
                (order_number.strip().upper(),)
            ).fetchone()

        return dict(row) if row else None

    @staticmethod
    def validate_order_ownership(order_number: str, customer_email: str) -> dict:
        """
        Validate that order exists and belongs to customer.

        Args:
            order_number: Order number to validate
            customer_email: Customer email to verify ownership

        Returns:
            Order dict if validation passes

        Raises:
            OrderNotFoundError: If order not found or doesn't belong to customer
        """
        order = OrderService.find_order_by_number(order_number)

        if not order:
            raise OrderNotFoundError(f"订单 {order_number} 不存在")

        # Verify ownership
        if order["customer_email"].lower() != customer_email.lower():
            raise OrderNotFoundError(f"订单 {order_number} 不属于该客户")

        return order

    @staticmethod
    def update_order_status(order_number: str, order_status: str = None,
                           shipping_status: str = None) -> bool:
        """
        Update order status.

        Args:
            order_number: Order number to update
            order_status: New order status (optional)
            shipping_status: New shipping status (optional)

        Returns:
            True if updated successfully
        """
        updates = []
        params = []

        if order_status:
            updates.append("order_status = ?")
            params.append(order_status)

        if shipping_status:
            updates.append("shipping_status = ?")
            params.append(shipping_status)

        if not updates:
            return False

        updates.append("updated_at = datetime('now')")
        params.append(order_number.strip().upper())

        with get_db_connection() as conn:
            conn.execute(
                f"UPDATE orders SET {', '.join(updates)} WHERE order_number = ?",
                params
            )
            conn.commit()

        return True

    @staticmethod
    def format_order_info(order: dict, language: str = "zh") -> str:
        """
        Format order information for email reply.

        Args:
            order: Order dict
            language: Language code (zh/en)

        Returns:
            Formatted order information string
        """
        if language == "zh":
            status_map = {
                "pending": "待确认",
                "confirmed": "已确认",
                "cancelled": "已取消",
                "refunded": "已退款"
            }
            shipping_map = {
                "not_shipped": "未发货",
                "in_transit": "运输中",
                "delivered": "已送达",
                "exception": "异常"
            }

            info = (
                f"- 订单号：{order['order_number']}\n"
                f"- 产品：{order['product_name']}\n"
                f"- 数量：{order['quantity']}\n"
                f"- 金额：{order['currency']} {order['total_amount']:.2f}\n"
                f"- 订单状态：{status_map.get(order['order_status'], order['order_status'])}\n"
                f"- 物流状态：{shipping_map.get(order['shipping_status'], order['shipping_status'])}"
            )

            if order.get('tracking_number'):
                info += f"\n- 物流单号：{order['tracking_number']}"
            if order.get('destination'):
                info += f"\n- 目的地：{order['destination']}"
        else:
            info = (
                f"- Order Number: {order['order_number']}\n"
                f"- Product: {order['product_name']}\n"
                f"- Quantity: {order['quantity']}\n"
                f"- Amount: {order['currency']} {order['total_amount']:.2f}\n"
                f"- Order Status: {order['order_status']}\n"
                f"- Shipping Status: {order['shipping_status']}"
            )

            if order.get('tracking_number'):
                info += f"\n- Tracking Number: {order['tracking_number']}"
            if order.get('destination'):
                info += f"\n- Destination: {order['destination']}"

        return info


# Singleton instance
_order_service = None


def get_order_service() -> OrderService:
    """Get singleton OrderService instance."""
    global _order_service
    if _order_service is None:
        _order_service = OrderService()
    return _order_service
