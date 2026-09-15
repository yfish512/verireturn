"""Add order-line and exchange inventory foundations.

Revision ID: 20260915_0023
Revises: 20260915_0022
"""
from alembic import op
import sqlalchemy as sa
revision="20260915_0023"; down_revision="20260915_0022"; branch_labels=None; depends_on=None
def upgrade():
 op.create_table("order_items",sa.Column("id",sa.String(36),primary_key=True),sa.Column("order_id",sa.String(32),sa.ForeignKey("orders.id"),nullable=False),sa.Column("sku",sa.String(64),nullable=False),sa.Column("title",sa.String(128),nullable=False),sa.Column("quantity",sa.Integer(),nullable=False),sa.Column("unit_amount",sa.Numeric(10,2),nullable=False),sa.Column("refunded_quantity",sa.Integer(),nullable=False,server_default="0"),sa.UniqueConstraint("order_id","sku",name="uq_order_item_sku"));op.create_index("ix_order_items_order_id","order_items",["order_id"])
 op.create_table("after_sales_items",sa.Column("id",sa.String(36),primary_key=True),sa.Column("case_id",sa.Integer(),sa.ForeignKey("after_sales_cases.id"),nullable=False),sa.Column("order_item_id",sa.String(36),sa.ForeignKey("order_items.id"),nullable=False),sa.Column("quantity",sa.Integer(),nullable=False),sa.Column("refund_amount",sa.Numeric(10,2),nullable=False),sa.UniqueConstraint("case_id","order_item_id",name="uq_after_sales_case_item"));op.create_index("ix_after_sales_items_case_id","after_sales_items",["case_id"]);op.create_index("ix_after_sales_items_order_item_id","after_sales_items",["order_item_id"])
 op.create_table("inventory_stock",sa.Column("sku",sa.String(64),primary_key=True),sa.Column("available_quantity",sa.Integer(),nullable=False,server_default="0"),sa.Column("reserved_quantity",sa.Integer(),nullable=False,server_default="0"),sa.Column("version",sa.Integer(),nullable=False,server_default="1"))
 op.create_table("inventory_reservations",sa.Column("id",sa.String(36),primary_key=True),sa.Column("case_id",sa.Integer(),sa.ForeignKey("after_sales_cases.id"),nullable=False),sa.Column("sku",sa.String(64),nullable=False),sa.Column("quantity",sa.Integer(),nullable=False),sa.Column("status",sa.String(16),nullable=False,server_default="reserved"),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("case_id",name="uq_inventory_reservation_case"));op.create_index("ix_inventory_reservations_case_id","inventory_reservations",["case_id"]);op.create_index("ix_inventory_reservations_sku","inventory_reservations",["sku"])
def downgrade():
 op.drop_table("inventory_reservations");op.drop_table("inventory_stock");op.drop_table("after_sales_items");op.drop_table("order_items")
