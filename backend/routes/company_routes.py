"""
Company information API routes.
Blueprint prefix: /api/company
"""

from flask import Blueprint, jsonify, request, session

from services.company_info_service import CompanyInfoService

company_bp = Blueprint("company", __name__)
company_info_svc = CompanyInfoService()


def _require_auth():
    token = session.get("access_token")
    if not token:
        return None
    return token


@company_bp.route("/company/products", methods=["GET"])
def list_products():
    if not _require_auth():
        return jsonify({"error": "Not authenticated"}), 401

    return jsonify(company_info_svc.get_catalog())


@company_bp.route("/company/products", methods=["PUT"])
def replace_products():
    if not _require_auth():
        return jsonify({"error": "Not authenticated"}), 401

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    payload = request.json or {}
    products = payload.get("products")
    if not isinstance(products, list):
        return jsonify({"error": "Field 'products' must be a list"}), 400

    try:
        normalized = company_info_svc.replace_products(products)
        return jsonify({
            "success": True,
            "count": len(normalized),
            "products": normalized,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@company_bp.route("/company/products", methods=["POST"])
def add_product():
    if not _require_auth():
        return jsonify({"error": "Not authenticated"}), 401

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    payload = request.json or {}
    try:
        created = company_info_svc.add_product(payload)
        return jsonify({"success": True, "product": created}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@company_bp.route("/company/products/<string:product_name>", methods=["PATCH"])
def upsert_product(product_name: str):
    if not _require_auth():
        return jsonify({"error": "Not authenticated"}), 401

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    payload = request.json or {}
    payload["product_name"] = product_name

    try:
        updated = company_info_svc.upsert_product(payload)
        return jsonify({"success": True, "product": updated})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@company_bp.route("/company/products/<string:product_name>", methods=["DELETE"])
def delete_product(product_name: str):
    if not _require_auth():
        return jsonify({"error": "Not authenticated"}), 401

    try:
        deleted = company_info_svc.delete_product(product_name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not deleted:
        return jsonify({"error": "Product not found"}), 404

    return jsonify({"success": True})
