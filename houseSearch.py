import json
import os
import time
import urllib.parse
import webbrowser
import pandas as pd
from homeharvest import scrape_property

# --- FILE PATHS ---
LOCATION_FILE = "location/location.txt"
OUTPUT_FILE = "peninsula_homes_for_sale.html"
SEEN_FILE = "seen_listings.json"

# --- DEFAULT FALLBACK VALUES ---
DEFAULT_CONFIG = {
    "MAX_PRICE": 1500000,
    "MIN_BEDROOMS": 3,
    "MAX_HOA": None,
    "HIDE_PENDING": False,
    "DOWN_PAYMENT_PCT": 0.20,
    "INTEREST_RATE": 0.0728,
    "LOAN_TERM_YEARS": 30,
    "PROPERTY_TAX_RATE": 0.0123,
    "ANNUAL_HOME_INSURANCE": 1800,
}

DEFAULT_LOCATIONS = [
    # "Daly City, CA",
    # "South San Francisco, CA",
    # "San Bruno, CA",
    # "Millbrae, CA",
    # "Burlingame, CA",
    "San Mateo, CA",
    "Redwood City, CA",
    "Menlo Park, CA",
    "Palo Alto, CA",
    "Mountain View, CA",
]


def parse_value(raw_val):
    """Convert string values from location.txt into None, bool, int, or float."""
    val = raw_val.strip()
    lower = val.lower()
    if lower in ("none", "null", ""):
        return None
    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        return val


def load_from_location_file(filepath):
    """Read both KEY=VALUE settings and city/ZIP lines from location.txt."""
    config = dict(DEFAULT_CONFIG)

    if not os.path.exists(filepath):
        print(f"'{filepath}' not found. Creating it with default settings and Peninsula cities...")
        parent_dir = os.path.dirname(filepath)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("# --- SEARCH SETTINGS ---\n")
            f.write(f"MAX_PRICE = {config['MAX_PRICE']}\n")
            f.write(f"MIN_BEDROOMS = {config['MIN_BEDROOMS']}\n")
            f.write(f"MAX_HOA = {config['MAX_HOA']}\n")
            f.write(f"HIDE_PENDING = {config['HIDE_PENDING']}\n\n")
            f.write("# --- MORTGAGE SETTINGS ---\n")
            f.write(f"DOWN_PAYMENT_PCT = {config['DOWN_PAYMENT_PCT']}\n")
            f.write(f"INTEREST_RATE = {config['INTEREST_RATE']}\n")
            f.write(f"LOAN_TERM_YEARS = {config['LOAN_TERM_YEARS']}\n")
            f.write(f"PROPERTY_TAX_RATE = {config['PROPERTY_TAX_RATE']}\n")
            f.write(f"ANNUAL_HOME_INSURANCE = {config['ANNUAL_HOME_INSURANCE']}\n\n")
            f.write("# --- LOCATIONS (one city or ZIP code per line) ---\n")
            f.write("\n".join(DEFAULT_LOCATIONS) + "\n")
        return config, list(DEFAULT_LOCATIONS)

    locations = []
    seen = set()

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#"):
                continue

            if "=" in cleaned:
                key, raw_val = cleaned.split("=", 1)
                key = key.strip().upper()
                if key in config:
                    config[key] = parse_value(raw_val)
            else:
                if cleaned not in seen:
                    locations.append(cleaned)
                    seen.add(cleaned)

    return config, locations


def load_seen_history(filepath):
    """Load previously seen listings as {listing_id: last_price}."""
    if not os.path.exists(filepath):
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {item: None for item in data}
    return data


def save_seen_history(history, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, sort_keys=True)


def get_listing_id(row):
    """Use property_id if available, otherwise fall back to property_url."""
    pid = row.get("property_id")
    if pd.notna(pid):
        return str(pid)
    url = row.get("property_url")
    return str(url) if pd.notna(url) else str(row.get("formatted_address", ""))


def format_listing_status(raw_status):
    """Normalize HomeHarvest status values into clean display labels."""
    if pd.isna(raw_status):
        return "Active"
    s = str(raw_status).strip().upper()
    if s in ("FOR_SALE", "ACTIVE"):
        return "Active"
    if "PENDING" in s:
        return "Pending"
    if "CONTINGENT" in s:
        return "Contingent"
    return s.replace("_", " ").title()


def estimate_monthly_payment(price, hoa_fee, config):
    """Calculate all-in monthly cost: Mortgage (P&I) + Property Tax + Insurance + HOA."""
    if pd.isna(price) or price <= 0:
        return None

    price_f = float(price)
    principal = price_f * (1 - config["DOWN_PAYMENT_PCT"])
    monthly_rate = config["INTEREST_RATE"] / 12
    num_payments = config["LOAN_TERM_YEARS"] * 12

    if monthly_rate == 0:
        mortgage = principal / num_payments
    else:
        mortgage = principal * (
            monthly_rate * (1 + monthly_rate) ** num_payments
        ) / ((1 + monthly_rate) ** num_payments - 1)

    monthly_tax = (price_f * config["PROPERTY_TAX_RATE"]) / 12
    monthly_insurance = config["ANNUAL_HOME_INSURANCE"] / 12
    hoa = float(hoa_fee) if pd.notna(hoa_fee) and float(hoa_fee) > 0 else 0.0

    return {
        "total": mortgage + monthly_tax + monthly_insurance + hoa,
        "mortgage": mortgage,
        "tax": monthly_tax,
        "insurance": monthly_insurance,
        "hoa": hoa,
    }


def search_peninsula_free(cities, max_price, min_beds):
    frames = []

    for city in cities:
        print(f"Searching homes for sale in {city}...")
        try:
            df = scrape_property(
                location=city,
                listing_type="for_sale",
                price_max=max_price,
                beds_min=min_beds,
                sort_by="list_price",
                parallel=False,
            )
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as e:
            print(f"Error fetching {city}: {e}")

        time.sleep(2)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    if "property_id" in combined.columns:
        combined = combined.drop_duplicates(subset=["property_id"])
    return combined


def save_to_html(df, seen_history, filename, config):
    df = df.copy()

    if "status" in df.columns:
        df["listing_status"] = df["status"].apply(format_listing_status)
    else:
        df["listing_status"] = "Active"

    if config["HIDE_PENDING"]:
        df = df[~df["listing_status"].isin(["Pending", "Contingent"])]

    if config["MAX_HOA"] is not None and "hoa_fee" in df.columns:
        df = df[df["hoa_fee"].isna() | (df["hoa_fee"] <= config["MAX_HOA"])]

    df["listing_key"] = df.apply(get_listing_id, axis=1)

    change_statuses = []
    price_drops = []
    prev_prices = []
    updated_history = dict(seen_history)

    for _, row in df.iterrows():
        key = row["listing_key"]
        curr_price = float(row["list_price"]) if pd.notna(row.get("list_price")) else None

        if key not in seen_history:
            change_statuses.append(2)  # New listing
            price_drops.append(0)
            prev_prices.append(None)
        else:
            old_price = seen_history.get(key)
            if old_price is not None and curr_price is not None and curr_price < old_price:
                change_statuses.append(1)  # Price drop
                price_drops.append(old_price - curr_price)
                prev_prices.append(old_price)
            else:
                change_statuses.append(0)  # Unchanged
                price_drops.append(0)
                prev_prices.append(None)

        if curr_price is not None:
            updated_history[key] = curr_price
        elif key not in updated_history:
            updated_history[key] = None

    df["change_status"] = change_statuses
    df["price_drop"] = price_drops
    df["prev_price"] = prev_prices

    df = df.sort_values(by=["change_status", "list_price"], ascending=[False, True])

    new_count = int((df["change_status"] == 2).sum())
    drop_count = int((df["change_status"] == 1).sum())
    rows_html = []

    for _, row in df.iterrows():
        c_status = row["change_status"]
        l_status = row["listing_status"]
        price_val = row.get("list_price")
        price_num = float(price_val) if pd.notna(price_val) else -1
        price_str = f"${int(price_val):,}" if pd.notna(price_val) else "N/A"

        if c_status == 2:
            row_class = ' class="new-listing"'
            price_cell = f'<span class="badge badge-new">NEW</span> {price_str}'
        elif c_status == 1:
            row_class = ' class="price-drop"'
            drop_str = f"-${int(row['price_drop']):,}"
            old_str = f"${int(row['prev_price']):,}"
            price_cell = (
                f'<span class="badge badge-drop">DROP {drop_str}</span> '
                f'{price_str} <span class="old-price">{old_str}</span>'
            )
        else:
            row_class = ""
            price_cell = price_str

        status_lower = l_status.lower()
        if status_lower == "active":
            status_pill = f'<span class="status-pill status-active">{l_status}</span>'
        elif status_lower in ("pending", "contingent"):
            status_pill = f'<span class="status-pill status-pending">{l_status}</span>'
        else:
            status_pill = f'<span class="status-pill status-other">{l_status}</span>'

        hoa_val = row.get("hoa_fee")
        if pd.notna(hoa_val) and float(hoa_val) > 0:
            hoa_num = float(hoa_val)
            hoa_str = f"${int(hoa_num):,}/mo"
        else:
            hoa_num = 0
            hoa_str = "None"

        est = estimate_monthly_payment(price_val, hoa_val, config)
        if est is not None:
            monthly_num = round(est["total"], 2)
            tooltip = (
                f"P&amp;I: ${int(round(est['mortgage'])):,} | "
                f"Tax: ${int(round(est['tax'])):,} | "
                f"Ins: ${int(round(est['insurance'])):,} | "
                f"HOA: ${int(round(est['hoa'])):,}"
            )
            monthly_str = f'<strong title="{tooltip}" class="est-total">${int(round(est["total"])):,}/mo</strong>'
        else:
            monthly_num = -1
            monthly_str = "N/A"

        beds_val = row.get("beds")
        beds_num = float(beds_val) if pd.notna(beds_val) else -1
        beds_str = int(beds_num) if beds_num >= 0 and beds_num.is_integer() else (beds_num if beds_num >= 0 else "N/A")

        baths_val = row.get("full_baths")
        baths_num = float(baths_val) if pd.notna(baths_val) else -1
        baths_str = int(baths_num) if baths_num >= 0 and baths_num.is_integer() else (baths_num if baths_num >= 0 else "N/A")

        sqft_val = row.get("sqft")
        sqft_num = float(sqft_val) if pd.notna(sqft_val) else -1
        sqft_str = f"{int(sqft_num):,}" if sqft_num >= 0 else "N/A"

        # Price per sq ft
        if price_num > 0 and sqft_num > 0:
            ppsf_num = round(price_num / sqft_num, 2)
            ppsf_str = f"${int(round(ppsf_num)):,}"
        else:
            ppsf_num = -1
            ppsf_str = "N/A"

        # Days on market
        dom_val = row.get("days_on_mls")
        if pd.isna(dom_val):
            dom_val = row.get("days_on_market")
        if pd.notna(dom_val):
            dom_num = int(float(dom_val))
            dom_str = f"{dom_num}d"
        else:
            dom_num = -1
            dom_str = "N/A"

        address = str(row.get("formatted_address", "N/A"))
        url = row.get("property_url")
        photo_url = row.get("primary_photo")

        # Thumbnail photo (clickable if listing URL exists)
        if pd.notna(photo_url) and str(photo_url).startswith("http"):
            img_tag = f'<img src="{photo_url}" class="thumb" loading="lazy" alt="Home photo">'
            photo_cell = f'<a href="{url}" target="_blank">{img_tag}</a>' if pd.notna(url) else img_tag
        else:
            photo_cell = '<div class="no-photo">No Photo</div>'

        # Listing + Google Maps links
        links = []
        if pd.notna(url):
            links.append(f'<a href="{url}" target="_blank">Listing</a>')
        if address and address != "N/A":
            maps_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote_plus(address)}"
            links.append(f'<a href="{maps_url}" target="_blank">Map</a>')
        links_cell = " &bull; ".join(links) if links else "N/A"

        rows_html.append(
            f'<tr{row_class} data-status="{status_lower}">'
            f"<td>{photo_cell}</td>"
            f'<td data-sort="{price_num}">{price_cell}</td>'
            f'<td data-sort="{status_lower}">{status_pill}</td>'
            f'<td data-sort="{monthly_num}">{monthly_str}</td>'
            f'<td data-sort="{hoa_num}">{hoa_str}</td>'
            f'<td data-sort="{beds_num}">{beds_str}</td>'
            f'<td data-sort="{baths_num}">{baths_str}</td>'
            f'<td data-sort="{sqft_num}">{sqft_str}</td>'
            f'<td data-sort="{ppsf_num}">{ppsf_str}</td>'
            f'<td data-sort="{dom_num}">{dom_str}</td>'
            f'<td data-sort="{address.lower()}">{address}</td>'
            f'<td class="links-cell">{links_cell}</td>'
            f"</tr>"
        )

    rate_pct = f"{config['INTEREST_RATE'] * 100:.2f}%"
    down_pct = f"{int(config['DOWN_PAYMENT_PCT'] * 100)}%"
    tax_pct = f"{config['PROPERTY_TAX_RATE'] * 100:.2f}%"
    loan_years = config["LOAN_TERM_YEARS"]
    annual_ins = config["ANNUAL_HOME_INSURANCE"]
    total_count = len(df)

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Peninsula Homes for Sale</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; background: #f9f9f9; }}
        h1 {{ color: #222; margin-bottom: 5px; }}
        .summary {{ color: #555; margin-bottom: 16px; font-size: 15px; line-height: 1.6; }}
        .controls {{ display: flex; align-items: center; gap: 18px; margin-bottom: 16px; flex-wrap: wrap; }}
        #searchInput {{
            padding: 10px 14px;
            width: 340px;
            max-width: 100%;
            font-size: 14px;
            border: 1px solid #ccc;
            border-radius: 6px;
            box-shadow: inset 0 1px 2px rgba(0,0,0,0.05);
        }}
        #searchInput:focus {{ outline: none; border-color: #2c3e50; }}
        .toggle-label {{ font-size: 14px; color: #333; cursor: pointer; display: flex; align-items: center; gap: 6px; user-select: none; }}
        #visibleCount {{ color: #555; font-size: 14px; }}
        table {{ border-collapse: collapse; width: 100%; background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.1); font-size: 14px; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #ddd; vertical-align: middle; }}
        th {{ background-color: #2c3e50; color: white; user-select: none; white-space: nowrap; }}
        th.sortable {{ cursor: pointer; }}
        th.sortable:hover {{ background-color: #34495e; }}
        .sort-arrow {{ font-size: 11px; margin-left: 4px; opacity: 0.7; }}
        tr:hover {{ background-color: #f1f5f9; }}
        tr.new-listing {{ background-color: #ecfdf5; }}
        tr.new-listing:hover {{ background-color: #d1fae5; }}
        tr.price-drop {{ background-color: #fffbeb; }}
        tr.price-drop:hover {{ background-color: #fef3c7; }}
        .thumb {{
            width: 84px;
            height: 60px;
            object-fit: cover;
            border-radius: 4px;
            display: block;
            border: 1px solid #ddd;
        }}
        .no-photo {{
            width: 84px;
            height: 60px;
            background: #e5e7eb;
            color: #6b7280;
            font-size: 11px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 4px;
        }}
        .badge {{
            color: white;
            font-size: 11px;
            font-weight: bold;
            padding: 2px 6px;
            border-radius: 4px;
            margin-right: 6px;
            vertical-align: middle;
        }}
        .badge-new {{ background-color: #10b981; }}
        .badge-drop {{ background-color: #d97706; }}
        .status-pill {{
            font-size: 12px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 12px;
            display: inline-block;
        }}
        .status-active {{ background-color: #dbeafe; color: #1d4ed8; }}
        .status-pending {{ background-color: #f3f4f6; color: #6b7280; }}
        .status-other {{ background-color: #ede9fe; color: #6d28d9; }}
        .old-price {{ color: #888; text-decoration: line-through; font-size: 12px; margin-left: 6px; }}
        .est-total {{ cursor: help; border-bottom: 1px dotted #888; }}
        .links-cell {{ white-space: nowrap; }}
        a {{ color: #0066cc; text-decoration: none; font-weight: 500; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <h1>Michael Research Peninsula Homes for Sale</h1>
    <div class="summary">
        {total_count} total matches &bull;
        <strong>{new_count} new</strong> &bull;
        <strong>{drop_count} price drops</strong> since your last run<br>
        <small>Est. Monthly includes P&amp;I ({down_pct} down, {loan_years}-yr @ {rate_pct}) + {tax_pct} CA property tax + ${annual_ins:,}/yr insurance + HOA. Hover over any monthly figure for the breakdown.</small>
    </div>

    <div class="controls">
        <input type="text" id="searchInput" placeholder="Filter by city, address, NEW, or DROP..." onkeyup="filterTable()">
        <label class="toggle-label">
            <input type="checkbox" id="hidePendingToggle" onchange="filterTable()">
            Hide Pending / Contingent
        </label>
        <span id="visibleCount">Showing {total_count} of {total_count}</span>
    </div>

    <table id="homesTable">
        <thead>
            <tr>
                <th>Photo</th>
                <th class="sortable" onclick="sortTable(1, 'num', this)">Price <span class="sort-arrow">⇅</span></th>
                <th class="sortable" onclick="sortTable(2, 'str', this)">Status <span class="sort-arrow">⇅</span></th>
                <th class="sortable" onclick="sortTable(3, 'num', this)">Est. Monthly <span class="sort-arrow">⇅</span></th>
                <th class="sortable" onclick="sortTable(4, 'num', this)">HOA <span class="sort-arrow">⇅</span></th>
                <th class="sortable" onclick="sortTable(5, 'num', this)">Beds <span class="sort-arrow">⇅</span></th>
                <th class="sortable" onclick="sortTable(6, 'num', this)">Baths <span class="sort-arrow">⇅</span></th>
                <th class="sortable" onclick="sortTable(7, 'num', this)">Sq Ft <span class="sort-arrow">⇅</span></th>
                <th class="sortable" onclick="sortTable(8, 'num', this)">$/Sq Ft <span class="sort-arrow">⇅</span></th>
                <th class="sortable" onclick="sortTable(9, 'num', this)">DOM <span class="sort-arrow">⇅</span></th>
                <th class="sortable" onclick="sortTable(10, 'str', this)">Address <span class="sort-arrow">⇅</span></th>
                <th>Links</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows_html)}
        </tbody>
    </table>

    <script>
        let currentSortCol = -1;
        let sortAsc = true;

        function filterTable() {{
            const query = document.getElementById("searchInput").value.toLowerCase().trim();
            const hidePending = document.getElementById("hidePendingToggle").checked;
            const tbody = document.querySelector("#homesTable tbody");
            const rows = Array.from(tbody.querySelectorAll("tr"));
            let visible = 0;

            rows.forEach(row => {{
                const text = row.innerText.toLowerCase();
                const status = row.getAttribute("data-status") || "";
                const matchesSearch = text.includes(query);
                const matchesStatus = !(hidePending && (status === "pending" || status === "contingent"));

                const show = matchesSearch && matchesStatus;
                row.style.display = show ? "" : "none";
                if (show) visible++;
            }});

            document.getElementById("visibleCount").textContent =
                `Showing ${{visible}} of ${{rows.length}}`;
        }}

        function sortTable(colIndex, type, headerEl) {{
            const tbody = document.querySelector("#homesTable tbody");
            const rows = Array.from(tbody.querySelectorAll("tr"));

            if (currentSortCol === colIndex) {{
                sortAsc = !sortAsc;
            }} else {{
                currentSortCol = colIndex;
                sortAsc = true;
            }}

            rows.sort((a, b) => {{
                const aVal = a.children[colIndex].getAttribute("data-sort") || "";
                const bVal = b.children[colIndex].getAttribute("data-sort") || "";

                if (type === "num") {{
                    const aNum = parseFloat(aVal);
                    const bNum = parseFloat(bVal);
                    return sortAsc ? aNum - bNum : bNum - aNum;
                }} else {{
                    return sortAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
                }}
            }});

            rows.forEach(row => tbody.appendChild(row));

            document.querySelectorAll("th.sortable .sort-arrow").forEach(span => {{
                span.textContent = "⇅";
            }});
            headerEl.querySelector(".sort-arrow").textContent = sortAsc ? "▲" : "▼";
        }}
    </script>
</body>
</html>
"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(full_html)

    return updated_history, new_count, drop_count


if __name__ == "__main__":
    config, locations = load_from_location_file(LOCATION_FILE)

    if not locations:
        print(f"No locations found in '{LOCATION_FILE}'. Add at least one city or ZIP code and try again.")
    else:
        print(
            f"Loaded {len(locations)} locations from {LOCATION_FILE} "
            f"(Max Price: ${config['MAX_PRICE']:,}, Min Beds: {config['MIN_BEDROOMS']})"
        )
        seen_history = load_seen_history(SEEN_FILE)
        results = search_peninsula_free(locations, config["MAX_PRICE"], config["MIN_BEDROOMS"])

        if results.empty:
            print("No homes found matching your criteria.")
        else:
            updated_history, new_count, drop_count = save_to_html(
                results, seen_history, OUTPUT_FILE, config
            )
            save_seen_history(updated_history, SEEN_FILE)

            print(
                f"\n--- Done! {len(results)} total listings "
                f"({new_count} new, {drop_count} price drops) ---"
            )
            print(f"Saved results to {OUTPUT_FILE}. Opening in your browser...")
            webbrowser.open(fg"file://{os.path.abspath(OUTPUT_FILE)}")