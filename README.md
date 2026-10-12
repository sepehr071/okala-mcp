<!-- mcp-name: io.github.sepehr071/okala-mcp -->

<div align="center">

# okala-mcp: Okala MCP server for AI agents 🛒

**Unofficial, read-only MCP server that lets your AI agent do the grocery price check on Okala (اُکالا), an Iranian online supermarket.**<br>
Find the supermarkets, bakeries and fruit shops that deliver to your address, compare the same product<br>
across nearby stores, check fees, minimum order and delivery slots, and catch today's deals, from Claude, Cursor or Copilot.

[![PyPI](https://img.shields.io/pypi/v/okala-mcp?color=2563eb)](https://pypi.org/project/okala-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/okala-mcp)](https://pypi.org/project/okala-mcp/)
[![CI](https://github.com/sepehr071/okala-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/okala-mcp/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.sepehr071%2Fokala--mcp-7c3aed)](https://registry.modelcontextprotocol.io/?q=okala-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/okala-mcp/blob/main/LICENSE)

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=okala&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJva2FsYS1tY3AiXX0=)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_okala--mcp-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=okala&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22okala-mcp%22%5D%7D)

[Quick start](#quick-start) · [For AI agents](#for-ai-agents) · [What it can do](#what-it-can-do) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

</div>

---

## For AI agents

- **What:** unofficial MCP server for Okala (okala.com, اُکالا), an Iranian online grocery marketplace where local supermarkets, bakeries and fruit shops deliver to an address. Queries work best in Persian; prices are in Toman.
- **Install:** `claude mcp add okala -- uvx okala-mcp` (stdio transport, needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.10+).
- **Auth:** none needed. An optional SMS login (`ok_login`) unlocks Okala's text search in `ok_search`. Env vars: `OKALA_MCP_PROXY` (HTTP proxy), `OKALA_MCP_TOKEN` (access token instead of the saved login).
- **Access:** read-only. The 12 shopping tools only read; there is no cart, order or review tool. `ok_login`, `ok_login_verify` and `ok_logout` send an SMS or change the saved login, so ask the user before calling them.
- **Tools:** 16, all prefixed `ok_`. Every location tool needs `lat` and `lon`, so start with `ok_locate`. Typical sequence: `ok_locate` → `ok_search` → `ok_compare`, or `ok_locate` → `ok_stores` → `ok_store_products`.
- **Use when:** finding which stores deliver to an address, comparing one product's price and fees across nearby stores, or checking delivery slots and today's deals.
- **Do not use when:** the user wants to order (no cart or checkout), or is outside Okala's delivery areas (Tehran and some other big cities).
- **Output:** compact JSON, prices in Toman (the API's Rial divided by 10). `final_price` is what the buyer pays, `price` is before discount, `discount_pct` is a whole percent, ratings are 0-5. Pass `lat` and `lon` from `ok_locate`, and `store_id` from `ok_stores`, between tools.
- **Machine-readable:** [`server.json`](server.json), and the catalog of all Iranian MCP servers at [awesome-iran-mcp](https://github.com/sepehr071/awesome-iran-mcp) ([TOOLS.md](https://github.com/sepehr071/awesome-iran-mcp/blob/main/TOOLS.md), [servers.json](https://github.com/sepehr071/awesome-iran-mcp/blob/main/servers.json), [llms.txt](https://github.com/sepehr071/awesome-iran-mcp/blob/main/llms.txt)).

## Why

On Okala every store sets its own price for the same carton of milk, and each one adds its own service and
packaging fee and has its own minimum order. The site shows one store at a time, and there is no search
box without logging in. An agent with `okala-mcp` checks every store that delivers to you in one go:

> **You:** Where is Kalleh lactose-free milk cheapest near Yousef Abad, Tehran?
>
> **Agent:** *calls* `ok_locate(address="یوسف آباد")` → `ok_search(query="شیر بدون لاکتوز کاله", ...)` → `ok_compare(category_slug="low-fat-milk", ...)`
>
> Kalleh fat-free, lactose-free milk (1 L) is sold by 17 open stores nearby. Cheapest: **یاران دریان گلریز** at
> 147,875 Toman (12% off 169,000), 1.6 km away, first slot today 08-09, plus 10,500 service and 3,000 packaging.
> The dearest store asks 180,000, so you save 32,125.

<sub>Real tool output from 2026-10-07; prices change all the time. Prices are in Toman.</sub>

## What it can do

- 📍 **Locate** an address (Persian) and list the stores that deliver there, with rating, distance, first slot and fees
- 🔎 **Find products** near you by name: Okala's own text search after an optional one-time login, otherwise matched to Okala's categories
- 💸 **Compare** the same product across every nearby store, cheapest store first, with each store's fees
- 🏪 **Browse one store**: a whole department sorted by price or discount, filtered by name
- 🧾 **Product details**: price, discount, stock, max per order, brand, images
- ⏰ **Store details**: minimum order, whether it serves your address now, every delivery slot of the day
- ⚡ **Deals**: today's deal rows near you or in one store, and the biggest discounts of a store
- 🔒 **Read-only by design**: no cart, no orders, no reviews posted (the optional login is only used for search)

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). No API key or account.

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add okala -- uvx okala-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "okala": { "command": "uvx", "args": ["okala-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Cursor</b></summary>

Click **Install in Cursor** above, or add the Claude Desktop block to `~/.cursor/mcp.json`.
</details>

<details>
<summary><b>VS Code (Copilot agent mode)</b></summary>

Click **Install in VS Code** above, or add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "okala": { "type": "stdio", "command": "uvx", "args": ["okala-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Anything else</b></summary>

It's a standard stdio MCP server: run `uvx okala-mcp`, or `pip install okala-mcp` and run `okala-mcp`.
</details>

Then just ask:

- "Which supermarkets near Vanak Square deliver within the hour, and what are their fees?"
- "Cheapest Kalleh full-fat milk near me, and is that store's minimum order a problem for one carton?"
- "What deals does store 11009 have today?"
- <span dir="rtl">ارزان&zwnj;ترین تن ماهی نزدیک میدان آرژانتین کدام فروشگاه است؟</span>

## How it works

```text
  AI agent  (Claude, Cursor, Copilot, ...)
      │
      │  MCP over stdio
      ▼
  okala-mcp  (runs on your machine)
      │
      │  HTTPS
      └──────▶  apigateway.okala.com   the public JSON API of okala.com
```

`okala-mcp` runs locally and calls the same public endpoints the okala.com website uses as a guest.
There's no hosted server in between, no API key, and nothing about you is sent anywhere else
(only the delivery point you ask about goes to Okala).

## Tools

<details open>
<summary><b>📍 Where and who delivers</b> (4)</summary>

| Tool | What it does |
|---|---|
| `ok_locate` | Address text → coordinates (candidates) plus the street address of the best hit |
| `ok_stores` | Stores that deliver to a point: type, rating, distance, first slot, service / packaging / delivery fee |
| `ok_store` | One store: serves this point?, minimum order, location, every delivery slot with availability |
| `ok_store_reviews` | Customer reviews of a store: stars, comment, liked / disliked reasons |
</details>

<details open>
<summary><b>🔎 Products and prices</b> (6)</summary>

| Tool | What it does |
|---|---|
| `ok_search` | Products for a query near a point or in one store: Okala's text search when logged in, matching categories as a guest; name matches first, then cheapest |
| `ok_compare` | The same product across nearby stores, cheapest store first, with each store's fees |
| `ok_store_products` | A whole category of one store, sorted, filtered by name (search inside one store) |
| `ok_product` | One product in one store: price, discount, stock, max per order, brand, images |
| `ok_categories` | Category tree with slugs, near a point or in one store |
| `ok_brands` | Brand ids and slugs (featured brands, or lookup by slug) |
</details>

<details open>
<summary><b>⚡ Deals</b> (2)</summary>

| Tool | What it does |
|---|---|
| `ok_deals` | Deal rows running now near a point or in one store, with end time and top products |
| `ok_offer` | One deal row in full, across stores or in one store, sorted and paged |
</details>

<details open>
<summary><b>🔑 Optional login</b> (4)</summary>

| Tool | What it does |
|---|---|
| `ok_login` | Sends an SMS code to the user's phone; asks for the code in a form when the client supports it |
| `ok_login_verify` | Finishes the login with the code the user typed in the chat |
| `ok_account` | Logged in or guest, and as which phone (masked) |
| `ok_logout` | Deletes the saved login; the server continues as a guest |
</details>

The 12 shopping tools are annotated `readOnlyHint: true`. `ok_login`, `ok_login_verify` and `ok_logout` are not
(they send an SMS or change the saved login), so your client asks before running them. Every tool returns compact
structured JSON, so it doesn't flood the agent's context.

## Good to know

- **Prices are in Toman.** The API speaks Rial; every tool divides by 10. `final_price` is what you pay, `price` is before discount, `discount_pct` is a whole percent.
- **Every store has its own price.** A product id is the same item in every store, so `ok_compare` can line them up.
- **Order cost** at one store = items + service fee + packaging fee + delivery fee (`ok_stores`), and the basket must reach the store's `minimum_order` (`ok_store`). One basket = one store.
- **Text search needs a login.** Okala only searches for logged-in users. Log in once (see [Optional login](#optional-login)) and `ok_search` uses Okala's real search, near you or inside one store (`store_id`), brands and product names included (`نوتلا`). As a guest, `ok_search` matches your words to category names (`شیر` → milk) and ranks products by name; for one store, `ok_store_products` with `name_contains` reads the whole department.
- **Up to 12 products per store** come back from the cross-store lists; use a narrower category slug or a brand id for more precise comparisons.
- **Persian works best** (`شیر کم چرب`, `پنیر`, `تن ماهی`). Arabic ي / ك and half-spaces are normalized.

## FAQ

<details>
<summary><b>Can it place an order for me?</b></summary>

No, and that's deliberate. It never touches the cart, order, payment, address or review endpoints, even when logged in.
The agent finds the best option; you buy it on okala.com.
</details>

<details>
<summary><b>Is there an Okala API or MCP server for AI agents?</b></summary>

Okala doesn't publish an official API for agents. `okala-mcp` is an unofficial MCP server that wraps the public
endpoints of okala.com as 16 tools (12 read-only shopping tools and 4 optional login tools).
</details>

<details>
<summary><b>How do I use Okala with Claude, Cursor or Copilot?</b></summary>

Install the server once (see [Quick start](#quick-start)), then ask in plain language, in Persian or English, with an
address or area so it can find the stores that deliver there. In Claude Code the install is
`claude mcp add okala -- uvx okala-mcp`.
</details>

<details>
<summary><b>Is it official, safe and free?</b></summary>

It is unofficial and free (MIT). Shopping tools are read-only and need no account or API key. It runs on your machine
with no hosted server in between; the optional login is saved only on your machine.
</details>

<details>
<summary><b>Why does <code>ok_search</code> show products that don't match my words?</b></summary>

As a guest (`mode: "category"`), Okala's text search is not available, so `ok_search` fetches the categories whose
names match your words and puts the products whose names contain your words first. Log in once to get real text search. `categories` in the reply shows what was used; pass a better
slug to `ok_compare`, or search one store's department with `ok_store_products`.
</details>

<details>
<summary><b>It says no store delivers to my point</b></summary>

Okala covers Tehran and some other big cities. Check the coordinates with `ok_locate` (the first candidate can be a
similar name in another district) and try again; `ok_stores` with `open_only: false` also shows stores that are closed now.
</details>

<details>
<summary><b>I get "Could not reach okala.com"</b></summary>

The server retries a dropped connection once. If it still fails, check your internet connection. System proxy
variables are ignored on purpose; set `OKALA_MCP_PROXY` if you need a proxy.
</details>

<details>
<summary><b>How do I debug what the agent sees?</b></summary>

```bash
npx @modelcontextprotocol/inspector uvx okala-mcp
```
</details>

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OKALA_MCP_PROXY` | unset | HTTP proxy for every request, e.g. `http://user:pass@host:port` |
| `OKALA_MCP_TOKEN_FILE` | `~/.okala-mcp/token.json` | Where `okala-mcp login` saves the login |
| `OKALA_MCP_TOKEN` | unset | An Okala access token to use instead of the saved login (not refreshed) |

## Optional login

Everything works as a guest. Logging in only unlocks Okala's text search in `ok_search` (brands and product names,
inside one store too). Just ask your agent:

> **You:** Log me in to Okala, my number is 0912 123 4567
>
> **Agent:** *calls* `ok_login` → Okala sends you an SMS → you type the code (in a form if your client shows one,
> otherwise in the chat) → logged in

- The code is sent only when you ask, at most once every 2 minutes per number.
- If your client supports MCP forms (elicitation), the code goes straight to the server and never passes through
  the model.
- The login is saved on your machine (`~/.okala-mcp/token.json`), refreshes itself, is sent only to
  apigateway.okala.com, and never appears in a tool reply. If it stops working, the server carries on as a guest.
- `ok_account` shows the state; `ok_logout` deletes the login.

Prefer a terminal? The same login works from the command line:

```bash
uvx okala-mcp login 09121234567          # sends the code
uvx okala-mcp login 09121234567 12345    # saves the login
uvx okala-mcp logout                     # deletes it
```

Restart your MCP client after a command-line login (a running server reads the login once).

<a id="فارسی"></a>

## فارسی: سرور MCP اُکالا

<div dir="rtl">

**okala-mcp** یک سرور MCP غیررسمی برای سوپرمارکت اینترنتی ایرانی اُکالا است و به دستیار هوش مصنوعی شما (Claude، Cursor، Copilot و ...) اجازه می&zwnj;دهد فروشگاه&zwnj;های اُکالا را که به
آدرس شما ارسال دارند پیدا کند، قیمت یک کالا را در همه&zwnj;ی فروشگاه&zwnj;های نزدیک مقایسه کند، هزینه&zwnj;ی خدمات و بسته&zwnj;بندی،
حداقل سفارش و زمان&zwnj;های ارسال را ببیند و تخفیف&zwnj;های امروز را پیدا کند.

- فقط خواندنی است: وارد حساب نمی&zwnj;شود، سبد خرید نمی&zwnj;سازد و سفارش ثبت نمی&zwnj;کند.
- قیمت&zwnj;ها به تومان است.
- روی سیستم خود شما اجرا می&zwnj;شود و به هیچ سرور واسطی داده نمی&zwnj;فرستد.

**نصب در Claude Code:**

</div>

```bash
claude mcp add okala -- uvx okala-mcp
```

<div dir="rtl">

بعد بپرسید: «ارزان&zwnj;ترین شیر کم چرب میهن نزدیک یوسف&zwnj;آباد تهران را کدام فروشگاه دارد؟»

برای جستجو کافی است نام کالا و محله یا آدرس خود را به فارسی بنویسید، مثلاً «تن ماهی نزدیک میدان آرژانتین».

</div>

## Development

```bash
git clone https://github.com/sepehr071/okala-mcp && cd okala-mcp
uv sync
uv run pytest            # offline, against recorded responses
uv run pytest -m live    # real okala.com
uv run ruff check .
```

Tools live in `src/okala_mcp/stores.py`, `catalog.py` and `offers.py`; each is a typed async function with a
docstring that tells the agent when to use it. Issues and PRs are welcome, especially new tools and fixes for site changes.

Releases: bump the version in `pyproject.toml` and `server.json`, then push a `v*` tag. GitHub Actions tests,
publishes to PyPI and the [MCP Registry](https://registry.modelcontextprotocol.io), and creates the GitHub Release.

## Related servers

- [Snappfood and SnappMarket MCP server](https://github.com/sepehr071/snapp-mcp) for food and grocery prices
- All Iranian MCP servers: [awesome-iran-mcp](https://github.com/sepehr071/awesome-iran-mcp)

If this saves you clicks, a star helps others find it.

## Disclaimer

Unofficial and not affiliated with or endorsed by Okala. It uses the public endpoints of the okala.com
website, which can change without notice. Please keep request rates reasonable.

## License

[MIT](https://github.com/sepehr071/okala-mcp/blob/main/LICENSE)
