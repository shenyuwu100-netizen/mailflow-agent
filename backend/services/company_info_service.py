"""
Company information service backed by a JSON file.
Stores and maintains product catalog for reply template enrichment.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import Config


class CompanyInfoService:
    """Read/write company product information from JSON datastore."""

    REQUIRED_FIELDS = {
        "product_name",
        "unit_price",
        "min_order_quantity",
        "delivery_lead_time_days",
    }

    def __init__(self, file_path: str | None = None):
        self.file_path = Path(file_path or Config.COMPANY_PRODUCTS_PATH)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        if self.file_path.exists():
            return

        default_payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "products": [],
        }
        self._write(default_payload)

    def _read(self) -> dict[str, Any]:
        with self.file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if "products" not in data or not isinstance(data["products"], list):
            data["products"] = []
        return data

    def _write(self, payload: dict[str, Any]) -> None:
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self.file_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")

    def _normalize_product(self, product: dict[str, Any]) -> dict[str, Any]:
        missing = self.REQUIRED_FIELDS - set(product.keys())
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(sorted(missing))}")

        normalized = {
            "product_name": str(product["product_name"]).strip(),
            "unit_price": float(product["unit_price"]),
            "currency": str(product.get("currency", "USD")).strip().upper(),
            "min_order_quantity": int(product["min_order_quantity"]),
            "delivery_lead_time_days": int(product["delivery_lead_time_days"]),
        }

        if not normalized["product_name"]:
            raise ValueError("product_name cannot be empty")
        if normalized["unit_price"] < 0:
            raise ValueError("unit_price must be >= 0")
        if normalized["min_order_quantity"] <= 0:
            raise ValueError("min_order_quantity must be > 0")
        if normalized["delivery_lead_time_days"] <= 0:
            raise ValueError("delivery_lead_time_days must be > 0")

        return normalized

    def list_products(self) -> list[dict[str, Any]]:
        return self._read()["products"]

    def replace_products(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = [self._normalize_product(p) for p in products]
        payload = self._read()
        payload["products"] = normalized
        self._write(payload)
        return normalized

    def add_product(self, product: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_product(product)
        payload = self._read()

        existing_names = {p.get("product_name", "").lower() for p in payload["products"]}
        if normalized["product_name"].lower() in existing_names:
            raise ValueError("product_name already exists")

        payload["products"].append(normalized)
        self._write(payload)
        return normalized

    def upsert_product(self, product: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_product(product)
        payload = self._read()

        updated = False
        for i, existing in enumerate(payload["products"]):
            if str(existing.get("product_name", "")).lower() == normalized["product_name"].lower():
                payload["products"][i] = normalized
                updated = True
                break

        if not updated:
            payload["products"].append(normalized)

        self._write(payload)
        return normalized

    def delete_product(self, product_name: str) -> bool:
        name = (product_name or "").strip().lower()
        if not name:
            raise ValueError("product_name cannot be empty")

        payload = self._read()
        original_count = len(payload["products"])
        payload["products"] = [
            p for p in payload["products"]
            if str(p.get("product_name", "")).strip().lower() != name
        ]

        if len(payload["products"]) == original_count:
            return False

        self._write(payload)
        return True

    def get_catalog(self) -> dict[str, Any]:
        data = self._read()
        return {
            "version": data.get("version", 1),
            "updated_at": data.get("updated_at"),
            "products": data.get("products", []),
        }
