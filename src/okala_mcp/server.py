"""MCP server entry point: registers every read-only Okala tool."""

import logging
import sys

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, account, auth, catalog, offers, stores  # noqa: F401  (imports register the tools)
from .registry import TOOLS

INSTRUCTIONS = """\
Unofficial, read-only access to Okala (www.okala.com), an Iranian online grocery marketplace: local
supermarkets, bakeries, fruit shops and chain stores deliver to the customer's address. Everything is
location-based. Nothing here can fill a cart, order or post reviews.

Login is optional and only unlocks Okala's real text search. When ok_search answers in "category" mode
and the user wants better search, offer to log in: ok_login with the user's own phone sends an SMS
code (the client may show a form for it; otherwise ask the user for the code and call
ok_login_verify). Never log in without the user asking for it. ok_account shows the state, ok_logout ends it.

Workflow:
1. ok_locate: address text -> lat / lon (every other tool needs the point; there is no default).
2. ok_stores: stores that deliver there, with rating, earliest slot and per-order fees.
3. Find products:
   - ok_search: "how much is X near me" (or in one store with store_id). Its `mode` says how it ran:
     "text" = Okala's own search (the user logged in); "category" = guest fallback that matches the
     query to category names, so check the `categories` it used and refine with a slug.
   - ok_compare: the same product across nearby stores, cheapest store first (category slug or brand id).
   - ok_store_products: one store's whole category, sorted, with name_contains (search inside one store).
   - ok_categories / ok_brands: slugs and brand ids.
4. ok_product: one product in one store (stock, max per order, images, description).
5. ok_deals / ok_offer: deal rows near the point or in one store, and one row in full.
   ok_store: minimum order, delivery slots; ok_store_reviews: what customers say.

Conventions: all prices are Toman (the API's Rial / 10). final_price is what the customer pays, price
is before discount, discount_pct is an int. Prices and stock are per store; a product id is the same
item in every store. True cost of an order at one store = items + service_fee + packaging_fee +
delivery_fee (ok_stores / ok_compare), and the basket must reach that store's minimum_order (ok_store).
A basket comes from one store; compare whole baskets, not single items, when the user buys several things.
Badge 'kalabarg' = can be paid with the government voucher (kalabarg). Delivery slots and dates are
Tehran local time; deal row end times are UTC.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

mcp = MCPServer(
    "okala-mcp",
    title="Okala",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/sepehr071/okala-mcp",
)

# The login tools send an SMS or change the saved login: not read-only, so clients ask before running them.
ACCOUNT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)

for fn, title, read_only in TOOLS:
    mcp.add_tool(fn, title=title, annotations=READ_ONLY if read_only else ACCOUNT)


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request floods client logs
    if sys.argv[1:2] in (["login"], ["logout"]):
        auth.cli(sys.argv[1:])
        return
    mcp.run()


if __name__ == "__main__":
    main()
